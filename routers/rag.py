from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
import voyageai
import anthropic
import asyncpg
import json
import os
import tempfile
from database import get_pool
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

vo = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))
ac = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a BODIVA capital markets research assistant for Utanya.

Rules you must follow without exception:
1. Answer ONLY using the provided context documents. Never use prior knowledge.
2. For every factual claim, cite the source (document title, section, date).
3. If the context does not contain enough information, say explicitly:
   "Não tenho essa informação na minha base de conhecimento."
4. Never give personalized investment advice or recommendations.
5. For anything transactional (buying, selling, account opening),
   direct the user to their SCVM intermediary.
6. If asked about current prices or yields, note the date of your latest bulletin.
7. Always respond in the same language the question was asked in.
8. When quoting numbers, always state the source document and date."""


# --- MODELS ---

class QueryRequest(BaseModel):
    question: str

class IngestTextRequest(BaseModel):
    title: str
    doc_type: str  # 'bulletin', 'legislation', 'regulation', 'report', 'note'
    content: str
    doc_date: str | None = None
    source_url: str | None = None
    metadata: dict = {}


# --- SETUP ---

@router.post("/setup")
async def setup_database():
    """Enable pgvector and create tables. Run once after creating the Railway Postgres."""
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

        # Semantic similarity index
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx
            ON rag_chunks
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)

        # Full-text search index for exact codes (OI15F31A, Artigo 79, etc.)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS rag_chunks_fts_idx
            ON rag_chunks
            USING gin(to_tsvector('portuguese', content))
        """)

    return {"status": "Database ready", "tables": ["rag_documents", "rag_chunks"]}


# --- INGEST ---

@router.post("/ingest/text")
async def ingest_text(request: IngestTextRequest):
    """Ingest a plain text document (legislation articles, notes, etc.)"""
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


@router.post("/ingest/pdf")
async def ingest_pdf(
    file: UploadFile = File(...),
    title: str = "",
    doc_type: str = "legislation",
    doc_date: str = None,
    source_url: str = None
):
    """Upload and ingest a PDF document."""
    import fitz  # pymupdf

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

        # Use filename as title if not provided
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
    """Ask a question and get a sourced answer from the knowledge base."""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    # Embed the question
    q_embedding = vo.embed(
        [request.question],
        model="voyage-3"
    ).embeddings[0]

    pool = await get_pool()
    async with pool.acquire() as conn:

        # Semantic search
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

        # Keyword search (catches exact codes like OI15F31A, Artigo 79, etc.)
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

    # Combine and deduplicate
    seen = set()
    all_chunks = []
    for row in list(semantic_results) + list(keyword_results):
        key = row['content'][:100]
        if key not in seen:
            seen.add(key)
            all_chunks.append(row)

    if not all_chunks:
        return {
            "answer": "Não tenho informação suficiente na base de conhecimento para responder a esta questão.",
            "sources": []
        }

    # Build context with citations
    context_parts = []
    sources = []
    for i, chunk in enumerate(all_chunks[:6]):
        date_str = f" ({chunk['doc_date']})" if chunk['doc_date'] else ""
        meta = json.loads(chunk['metadata']) if isinstance(chunk['metadata'], str) else chunk['metadata']
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

    # Generate answer with Claude
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


# --- DOCUMENTS LIST ---

@router.get("/documents")
async def list_documents():
    """List all documents in the knowledge base."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                d.id,
                d.title,
                d.doc_type,
                d.doc_date,
                d.source_url,
                d.created_at,
                COUNT(c.id) AS chunk_count
            FROM rag_documents d
            LEFT JOIN rag_chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.created_at DESC
        """)
    return [dict(row) for row in rows]


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int):
    """Remove a document and all its chunks."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM rag_documents WHERE id = $1", doc_id
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "document_id": doc_id}


# --- HELPERS ---

def chunk_text(text: str, base_metadata: dict = {}, chunk_size: int = 800, overlap: int = 100) -> list:
    """Split text into overlapping chunks."""
    # Try to split on article markers first (for legislation)
    import re
    article_pattern = re.compile(r'(Art(?:igo)?\.?\s*\d+\.?º?)', re.IGNORECASE)
    articles = article_pattern.split(text)

    chunks = []
    if len(articles) > 3:
        # Successfully found article structure
        i = 1
        while i < len(articles) - 1:
            article_header = articles[i]
            article_body = articles[i + 1] if i + 1 < len(articles) else ""
            content = (article_header + article_body).strip()
            if len(content) > 50:
                chunks.append({
                    "content": content[:1500],
                    "metadata": {**base_metadata, "article": article_header.strip()}
                })
            i += 2
    else:
        # Generic paragraph/size chunking
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


async def store_document_and_chunks(
    title: str,
    doc_type: str,
    doc_date: str | None,
    source_url: str | None,
    chunks: list
) -> int:
    """Embed all chunks and store everything in Postgres."""
    from datetime import date as date_type

    # Parse date
    parsed_date = None
    if doc_date:
        try:
            from datetime import datetime
            parsed_date = datetime.strptime(doc_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Embed all chunks in one API call
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
