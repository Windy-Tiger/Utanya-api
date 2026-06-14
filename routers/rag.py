import os
import json
import tempfile
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from dotenv import load_dotenv
from time_aware_retrieval import (
        classify_query, extract_dates, build_retrieval_sql,
        fetch_latest_summary_chunk
    )

load_dotenv()

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

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


# --- AUTH ---

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    expected = os.environ.get("API_SECRET_KEY")
    if not expected or api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return api_key


# --- CLIENTS ---

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
            ON rag_chunks USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS rag_chunks_fts_idx
            ON rag_chunks USING gin(to_tsvector('portuguese', content))
        """)
    return {"status": "Database ready", "tables": ["rag_documents", "rag_chunks"]}


# --- INGEST BULLETIN (structured parser) ---

@router.post("/ingest/bulletin")
@limiter.limit("5/minute")
async def ingest_bulletin(
    request: Request,
    file: UploadFile = File(...),
    api_key: str = Security(verify_api_key)
):
    """
    Upload a BODIVA bulletin PDF. Uses Claude vision to extract structured data.
    Each section (session summary, OT-NR bonds, stocks, repos, yield curve, etc.)
    becomes a precise, individually queryable chunk.
    """
    from bulletin_parser import parse_bulletin

    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        chunks, bulletin_num, date = parse_bulletin(tmp_path, anthropic_key)

        title = f"BODIVA Boletim {bulletin_num} - {date}"
        doc_id = await store_document_and_chunks(
            title=title,
            doc_type="bulletin",
            doc_date=date,
            source_url=None,
            chunks=chunks
        )

        return {
            "status": "ingested",
            "document_id": doc_id,
            "bulletin_number": bulletin_num,
            "date": date,
            "chunks_created": len(chunks),
            "title": title
        }
    finally:
        os.unlink(tmp_path)

# ADD THIS ROUTE to your existing routers/rag.py
# Place it after the /ingest/bulletin endpoint

@router.post("/ingest/bulletin-json")
@limiter.limit("10/minute")
async def ingest_bulletin_json(
    request: Request,
    file: UploadFile = File(...),
    api_key: str = Security(verify_api_key)
):
    """
    Ingest a pre-extracted bulletin JSON file.
    This is the preferred method: Claude reads the PDF natively in the conversation
    and produces a structured JSON, which is then ingested here with 100% accuracy.
    No vision API costs. No OCR errors.
    """
    from bulletin_json_converter import json_to_chunks

    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a JSON")

    contents = await file.read()

    try:
        bulletin_data = json.loads(contents)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    bulletin_num = bulletin_data.get("bulletin_number", "unknown")
    date = bulletin_data.get("date", "unknown")
    title = bulletin_data.get("title", f"BODIVA Boletim {bulletin_num} - {date}")

    chunks = json_to_chunks(bulletin_data)

    doc_id = await store_document_and_chunks(
        title=title,
        doc_type="bulletin",
        doc_date=date,
        source_url=None,
        chunks=chunks
    )

    return {
        "status": "ingested",
        "document_id": doc_id,
        "bulletin_number": bulletin_num,
        "date": date,
        "chunks_created": len(chunks),
        "title": title,
        "sections": list(set(c['metadata'].get('section', '') for c in chunks))
    }

# --- INGEST TEXT ---

@router.post("/ingest/text")
@limiter.limit("10/minute")
async def ingest_text(
    request: Request,
    body: IngestTextRequest,
    api_key: str = Security(verify_api_key)
):
    chunks = chunk_text(body.content, body.metadata)
    doc_id = await store_document_and_chunks(
        title=body.title,
        doc_type=body.doc_type,
        doc_date=body.doc_date,
        source_url=body.source_url,
        chunks=chunks
    )
    return {"status": "ingested", "document_id": doc_id, "chunks_created": len(chunks)}


# --- INGEST GENERIC PDF ---

@router.post("/ingest/pdf")
@limiter.limit("5/minute")
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(...),
    title: str = "",
    doc_type: str = "legislation",
    doc_date: str = None,
    source_url: str = None,
    api_key: str = Security(verify_api_key)
):
    """For non-bulletin PDFs: legislation, regulations, reports."""
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

        return {"status": "ingested", "document_id": doc_id, "chunks_created": len(chunks), "title": title}
    finally:
        os.unlink(tmp_path)


# --- QUERY ---


@router.post("/query")
@limiter.limit("10/minute")
async def query(
    request: Request,
    body: QueryRequest,
    api_key: str = Security(verify_api_key)
):
    from database import get_pool
 
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
 
    vo = get_voyage()
    ac = get_anthropic()
 
    qtype = classify_query(body.question)
    dates = extract_dates(body.question) if qtype in ("specific_date", "comparison") else None
 
    sql, extra_params, needs_embedding = build_retrieval_sql(qtype, dates, question=body.question)
 
    pool = await get_pool()
    async with pool.acquire() as conn:
 
        if needs_embedding:
            q_embedding = vo.embed([body.question], model="voyage-3").embeddings[0]
            semantic_results = await conn.fetch(sql, str(q_embedding), *extra_params)
        else:
            semantic_results = await conn.fetch(sql, *extra_params)
 
        # Fallback: specific_date / comparison found nothing (date not in KB yet)
        if qtype in ("specific_date", "comparison") and not semantic_results:
            qtype = "current_state"
            sql, extra_params, needs_embedding = build_retrieval_sql(qtype, question=body.question)
            q_embedding = vo.embed([body.question], model="voyage-3").embeddings[0]
            semantic_results = await conn.fetch(sql, str(q_embedding), *extra_params)
 
        # Safety net: always include the latest quadro_resumo for current_state
        if qtype == "current_state":
            latest = await fetch_latest_summary_chunk(conn)
            if latest:
                existing = {r['content'][:100] for r in semantic_results}
                if latest['content'][:100] not in existing:
                    semantic_results = [latest] + list(semantic_results)
 
       keyword_results = await conn.fetch("""
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   ts_rank(to_tsvector('portuguese', rc.content),
                           plainto_tsquery('portuguese', $1)) AS kw_rank
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            WHERE to_tsvector('portuguese', rc.content)
                  @@ plainto_tsquery('portuguese', $1)
            ORDER BY kw_rank DESC
            LIMIT 20
        """, body.question)
 
 # --- Reciprocal Rank Fusion (RRF) of semantic + keyword results ---
    # For date-anchored queries the semantic_results are already the
    # authoritative date-filtered set; we still fuse keyword hits but keep
    # the date results dominant by giving them a strong base rank.
    RRF_K = 60  # standard constant; dampens the influence of low ranks
 
    def _key(row):
        return row['content'][:120]
 
    rrf_scores = {}
    row_by_key = {}
 
    # Semantic list contributes by its order (best first)
    for rank, row in enumerate(list(semantic_results)):
        k = _key(row)
        row_by_key[k] = row
        rrf_scores[k] = rrf_scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
 
    # Keyword list contributes by its order (best first)
    for rank, row in enumerate(list(keyword_results)):
        k = _key(row)
        # Keep the row we already have (semantic rows carry a real
        # similarity score); only store keyword row if new.
        if k not in row_by_key:
            row_by_key[k] = row
        rrf_scores[k] = rrf_scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
 
    # Order by fused score, highest first
    all_chunks = [row_by_key[k] for k, _ in
                  sorted(rrf_scores.items(), key=lambda kv: kv[1], reverse=True)]
 
    if not all_chunks:
        return {
            "answer": "Nao tenho informacao suficiente na base de conhecimento para responder.",
            "sources": []
        }
 
    context_parts = []
    sources = []
      context_limit = 14 if qtype in ("specific_date", "comparison") else 10
    for i, chunk in enumerate(all_chunks[:context_limit]):
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
            "similarity": float(chunk['similarity']) if 'similarity' in chunk and chunk['similarity'] is not None else None
        })
 
    context = "\n\n---\n\n".join(context_parts)
 
    response = ac.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Context:\n\n{context}\n\n---\n\nQuestion: {body.question}"}]
    )
    return {"answer": response.content[0].text, "sources": sources, "query_type": qtype}
# --- LIST DOCUMENTS ---

@router.get("/documents")
async def list_documents(api_key: str = Security(verify_api_key)):
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT d.id, d.title, d.doc_type, d.doc_date, d.source_url, d.created_at,
                   COUNT(c.id) AS chunk_count
            FROM rag_documents d
            LEFT JOIN rag_chunks c ON c.document_id = d.id
            GROUP BY d.id ORDER BY d.created_at DESC
        """)
    return [dict(row) for row in rows]


# --- DELETE DOCUMENT ---

@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, api_key: str = Security(verify_api_key)):
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM rag_documents WHERE id = $1", doc_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "document_id": doc_id}


# --- HELPERS ---

def chunk_text(text: str, base_metadata: dict = {}, chunk_size: int = 800) -> list:
    import re
 
    # --- Normalize line endings FIRST (fixes the \r\n vs \n\n bug) ---
    text = text.replace("\r\n", "\n").replace("\r", "\n")
 
    chunks = []
 
    def pack_paragraphs(blob, meta):
        """Greedily pack paragraphs into ~chunk_size pieces. Never drops text:
        a single oversized paragraph is hard-split into chunk_size windows."""
        out = []
        paragraphs = [p.strip() for p in blob.split("\n\n") if p.strip()]
        # If there were no blank-line breaks at all, fall back to single-newline
        if len(paragraphs) <= 1:
            paragraphs = [p.strip() for p in blob.split("\n") if p.strip()]
        current = ""
        for para in paragraphs:
            # Hard-split any paragraph longer than chunk_size so nothing is lost
            while len(para) > chunk_size:
                if current.strip():
                    out.append(current.strip())
                    current = ""
                out.append(para[:chunk_size])
                para = para[chunk_size:]
            if len(current) + len(para) + 2 < chunk_size:
                current += ("\n\n" + para) if current else para
            else:
                if current.strip():
                    out.append(current.strip())
                current = para
        if current.strip():
            out.append(current.strip())
        return [{"content": c, "metadata": meta} for c in out]
 
    # --- Article-aware path for legislation that really has "Artigo N" ---
    article_pattern = re.compile(r'(Art(?:igo)?\.?\s*\d+\.?\s*\xba?)', re.IGNORECASE)
    parts = article_pattern.split(text)
 
    if len(parts) > 3:
        # parts[0] is any preamble BEFORE the first article -> keep it!
        preamble = parts[0].strip()
        if len(preamble) > 50:
            chunks.extend(pack_paragraphs(preamble, base_metadata))
 
        # Then walk header/body pairs (parts[1]=header, parts[2]=body, ...)
        i = 1
        while i < len(parts):
            header = parts[i].strip() if i < len(parts) else ""
            body = parts[i + 1] if i + 1 < len(parts) else ""
            content = (header + " " + body).strip()
            if len(content) > 50:
                meta = {**base_metadata, "article": header}
                # Pack long articles into multiple chunks instead of truncating
                if len(content) > chunk_size:
                    for c in pack_paragraphs(content, meta):
                        chunks.append(c)
                else:
                    chunks.append({"content": content, "metadata": meta})
            i += 2
    else:
        # --- Plain prose path (AGT text, BODIVA historia, reports) ---
        chunks = pack_paragraphs(text, base_metadata)
 
    # Final safety net: never return nothing, but also never return ONE giant
    # chunk for a long doc -- if we somehow got 1 chunk for >chunk_size text,
    # force a re-pack.
    if not chunks:
        chunks = pack_paragraphs(text, base_metadata)
    if len(chunks) == 1 and len(text) > chunk_size * 1.5:
        chunks = pack_paragraphs(text, base_metadata)
 
    return chunks if chunks else [{"content": text[:1500], "metadata": base_metadata}]

# REPLACE the store_document_and_chunks function at the bottom of routers/rag.py
# with this updated version that batches embeddings

async def store_document_and_chunks(title, doc_type, doc_date, source_url, chunks):
    """Embed all chunks in batches and store everything in Postgres."""
    from database import get_pool
    from datetime import datetime

    vo = get_voyage()

    parsed_date = None
    if doc_date and doc_date != "unknown":
        try:
            parsed_date = datetime.strptime(doc_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    texts = [c["content"] for c in chunks]

    # Voyage AI limit: 128 texts per request
    # Batch in groups of 100 to stay safely under the limit
    BATCH_SIZE = 100
    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        result = vo.embed(batch, model="voyage-3")
        all_embeddings.extend(result.embeddings)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            doc_id = await conn.fetchval("""
                INSERT INTO rag_documents (title, doc_type, doc_date, source_url)
                VALUES ($1, $2, $3, $4) RETURNING id
            """, title, doc_type, parsed_date, source_url)

            for chunk, embedding in zip(chunks, all_embeddings):
                vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
                await conn.execute("""
                    INSERT INTO rag_chunks (document_id, content, metadata, embedding)
                    VALUES ($1, $2, $3, $4::vector)
                """, doc_id, chunk["content"], json.dumps(chunk["metadata"]), vec_str)

    return doc_id