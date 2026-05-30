from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
import os
import json
import tempfile
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

SYSTEM_PROMPT = """You are a BODIVA capital markets research assistant for Utanya.

Rules you must follow without exception:
1. Answer ONLY using the provided context documents. Never use prior knowledge.
2. For every factual claim, cite the source (document title, section, date).
3. If the context does not contain enough information, say explicitly:
   "Nao tenho essa informacao na minha base de conhecimento."
4. Never give personalized investment advice or recommendations.
5. For anything transactional (buying, selling, account opening),
   direct the user to their SCVM intermediary.
6. If asked about current prices or yields, note the date of your latest bulletin.
7. Always respond in the same language the question was asked in.
8. When quoting numbers, always state the source document and date."""


def get_voyage():
    import voyageai
    return voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))

def get_anthropic():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


# --- MODELS ---

class QueryRequest(BaseModel):
    question: str

class IngestTextRequest(BaseModel):
    title: str
    doc_type: str
    content: str
    doc_date: str | None = None
    source_url: str | None = None
    metadata: dict = {}


# --- SETUP ---

@router.post("/setup")
async def setup_database():
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_documents (
                id          SERIAL PRIMARY KEY,
                title       TEXT NOT NULL,
                doc_type    TEXT NOT NULL,
                doc_date    DATE,
                source_url  TEXT,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id          SERIAL PRIMARY KEY,
                document_id INTEGER REFERENCES rag_documents(id) ON DELETE CASCADE,
                content     TEXT NOT NULL,
                metadata    JSONB DEFAULT '{}',
                embedding   vector(1024),
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx
            ON rag_chunks
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS rag_chunks_fts_idx
            ON rag_chunks
            USING gin(to_tsvector('portuguese', content))
        """)
    return {"status": "Database ready", "tables": ["rag_documents", "rag_chunks"]}


# --- INGEST TEXT ---

@router.post("/ingest/text")
async def ingest_text(request: IngestTextRequest):
    chunks = chunk_text(request.content, request.metadata)
    doc_id = await store_document_and_chunks(
        title=request.title,
        doc_type=request.doc_type,
        doc_date=request.doc_date,
        source_url=request.source_url,
        chunks=chunks
    )
    return {
        "status": "ingested",
        "document_id": doc_id,
        "chunks_created": len(chunks)
    }


# --- INGEST PDF ---

@router.post("/ingest/pdf")
async def ingest_pdf(
    file: UploadFile = File(...),
    title: str = "",
    doc_type: str = "legislation",
    doc_date: str = None,
    source_url: str = None
):
    import fitz

    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        doc = fitz.open(tmp_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n\n"
        doc.close()

        if not title:
            title = file.filename.replace(".pdf", "")

        chunks = chunk_text(full_text, {"source_file": file.filename})
        doc_id = await store_document_and_chunks(
            title=title,
            doc_type=doc_type,
            doc_date=doc_date,
            source_url=source_url,
            chunks=chunks
        )

        return {
            "status": "ingested",
            "document_id": doc_id,
            "chunks_created": len(chunks),
            "title": title
        }
    finally:
        os.unlink(tmp_path)


# --- QUERY ---

@router.post("/query")
async def query(request: QueryRequest):
    from database import get_pool

    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    vo = get_voyage()
    ac = get_anthropic()

    q_embedding = vo.embed(
        [request.question],
        model="voyage-3"
    ).embeddings[0]

    pool = await get_pool()
    async with pool.acquire() as conn:
        semantic_results = await conn.fetch("""
            SELECT
                rc.content,
                rc.metadata,
                rd.title,
                rd.doc_type,
                rd.doc_date,
                1 - (rc.embedding <=> $1::vector) AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            ORDER BY similarity DESC
            LIMIT 5
        """, str(q_embedding))

        keyword_results = await conn.fetch("""
            SELECT
                rc.content,
                rc.metadata,
                rd.title,
                rd.doc_type,
                rd.doc_date,
                0.5 AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            WHERE to_tsvector('portuguese', rc.content)
                  @@ plainto_tsquery('portuguese', $1)
            LIMIT 3
        """, request.question)

    seen = set()
    all_chunks = []
    for row in list(semantic_results) + list(keyword_results):
        key = row['content'][:100]
        if key not in seen:
            seen.add(key)
            all_chunks.append(row)

    if not all_chunks:
        return {
            "answer": "Nao tenho informacao suficiente na base de conhecimento para responder a esta questao.",
            "sources": []
        }

    context_parts = []
    sources = []
    for i, chunk in enumerate(all_chunks[:6]):
        date_str = f" ({chunk['doc_date']})" if chunk['doc_date'] else ""
        meta = chunk['metadata']
        if isinstance(meta, str):
            meta = json.loads(meta)
        section = meta.get('section') or meta.get('article') or ''
        citation = f"[{i+1}] {chunk['title']}{date_str}"
        if section:
            citation += f" — {section}"
        context_parts.append(f"{citation}:\n{chunk['content']}")
        sources.append({
            "ref": i + 1,
            "title": chunk['title'],
            "doc_type": chunk['doc_type'],
            "date": str(chunk['doc_date']) if chunk['doc_date'] else None,
            "section": section,
            "similarity": float(chunk['similarity'])
        })

    context = "\n\n---\n\n".join(context_parts)

    response = ac.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Context documents:\n\n{context}\n\n---\n\nQuestion: {request.question}"
        }]
    )

    return {
        "answer": response.content[0].text,
        "sources": sources
    }


# --- LIST DOCUMENTS ---

@router.get("/documents")
async def list_documents():
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                d.id, d.title, d.doc_type, d.doc_date,
                d.source_url, d.created_at,
                COUNT(c.id) AS chunk_count
            FROM rag_documents d
            LEFT JOIN rag_chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.created_at DESC
        """)
    return [dict(row) for row in rows]


# --- DELETE DOCUMENT ---

@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int):
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM rag_documents WHERE id = $1", doc_id
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "document_id": doc_id}


# --- HELPERS ---

def chunk_text(text: str, base_metadata: dict = {}, chunk_size: int = 800) -> list:
    import re
    article_pattern = re.compile(r'(Art(?:igo)?\.?\s*\d+\.?)', re.IGNORECASE)
    articles = article_pattern.split(text)

    chunks = []
    if len(articles) > 3:
        i = 1
        while i < len(articles) - 1:
            header = articles[i]
            body = articles[i + 1] if i + 1 < len(articles) else ""
            content = (header + body).strip()
            if len(content) > 50:
                chunks.append({
                    "content": content[:1500],
                    "metadata": {**base_metadata, "article": header.strip()}
                })
            i += 2
    else:
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        current = ""
        for para in paragraphs:
            if len(current) + len(para) < chunk_size:
                current += "\n\n" + para
            else:
                if current.strip():
                    chunks.append({
                        "content": current.strip(),
                        "metadata": base_metadata
                    })
                current = para
        if current.strip():
            chunks.append({
                "content": current.strip(),
                "metadata": base_metadata
            })

    return chunks if chunks else [{"content": text[:1500], "metadata": base_metadata}]


async def store_document_and_chunks(title, doc_type, doc_date, source_url, chunks):
    from database import get_pool
    from datetime import datetime

    vo = get_voyage()

    parsed_date = None
    if doc_date:
        try:
            parsed_date = datetime.strptime(doc_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    texts = [c["content"] for c in chunks]
    result = vo.embed(texts, model="voyage-3")
    embeddings = result.embeddings

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            doc_id = await conn.fetchval("""
                INSERT INTO rag_documents (title, doc_type, doc_date, source_url)
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """, title, doc_type, parsed_date, source_url)

            for chunk, embedding in zip(chunks, embeddings):
                await conn.execute("""
                    INSERT INTO rag_chunks (document_id, content, metadata, embedding)
                    VALUES ($1, $2, $3, $4::vector)
                """,
                doc_id,
                chunk["content"],
                json.dumps(chunk["metadata"]),
                str(embedding)
                )
    return doc_id