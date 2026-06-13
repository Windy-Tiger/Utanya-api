"""
time_aware_retrieval.py

Lightweight, zero-extra-cost query classification + adaptive retrieval
helpers for the Utanya RAG system.

Matches the actual schema in routers/rag.py:
  - rag_documents(id, title, doc_type, doc_date, source_url, created_at)
  - rag_chunks(id, document_id, content, metadata JSONB, embedding vector(1024), created_at)

Drop this file into your project root (alongside main.py / database.py)
and wire it into routers/rag.py per the integration notes at the bottom.

No new API calls beyond what /rag/query already makes. For specific_date
and comparison queries, the Voyage embedding call is SKIPPED entirely,
slightly REDUCING your existing Voyage usage.
"""

import re
from datetime import datetime, date
from typing import Literal

QueryType = Literal["current_state", "trend", "specific_date", "comparison"]


# ---------------------------------------------------------------------------
# 1. Query classification (free, regex/keyword based)
# ---------------------------------------------------------------------------

_CURRENT_KEYWORDS = [
    "atual", "actual", "agora", "hoje", "neste momento", "presente",
    "current", "now", "today", "última cotação", "ultima cotacao",
    "último boletim", "ultimo boletim", "mais recente", "neste boletim",
    "boletim de hoje"
]

_TREND_KEYWORDS = [
    "evolução", "evolucao", "tendência", "tendencia", "histórico", "historico",
    "ao longo", "desde", "trend", "history", "variação ao longo",
    "subiu", "desceu", "aumentou", "diminuiu", "média", "media",
    "vwap", "últimos meses", "ultimos meses"
]

_DATE_PATTERNS = [
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|"
    r"agosto|setembro|outubro|novembro|dezembro)\b",
]

_COMPARISON_KEYWORDS = [
    " vs ", " versus ", "comparado com", "em relação a", "em relacao a",
    "diferença entre", "diferenca entre", "comparar"
]


def classify_query(question: str) -> QueryType:
    """Classify a user question into a retrieval strategy bucket.

    Order: comparison > specific_date > current_state > trend > default.
    """
    q = question.lower()

    date_matches = []
    for pattern in _DATE_PATTERNS:
        date_matches.extend(re.findall(pattern, q))

    has_comparison_kw = any(kw in q for kw in _COMPARISON_KEYWORDS)
    has_multiple_dates = len(date_matches) >= 2

    if has_comparison_kw or has_multiple_dates:
        return "comparison"

    if date_matches:
        return "specific_date"

    if any(kw in q for kw in _CURRENT_KEYWORDS):
        return "current_state"

    if any(kw in q for kw in _TREND_KEYWORDS):
        return "trend"

    # Default: current_state. The safety-net latest-summary chunk makes
    # this low-risk even for ambiguous "what is X" questions.
    return "current_state"


# ---------------------------------------------------------------------------
# 2. Extract explicit dates from a query -> list of "YYYY-MM-DD" strings
# ---------------------------------------------------------------------------

_MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def extract_dates(question: str, default_year: int | None = None) -> list[str]:
    """Best-effort extraction of dates mentioned in the question.

    Handles "29/05/2026", "2026-05-29", and "29 de maio" (assumes
    default_year, or current year, if no year is given).
    """
    q = question.lower()
    dates = []
    year = default_year or datetime.now().year

    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", q):
        dates.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")

    for m in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", q):
        d, mo, y = m.groups()
        y = int(y)
        if y < 100:
            y += 2000
        dates.append(f"{y:04d}-{int(mo):02d}-{int(d):02d}")

    for m in re.finditer(
        r"\b(\d{1,2})\s+de\s+(janeiro|fevereiro|março|marco|abril|maio|junho|"
        r"julho|agosto|setembro|outubro|novembro|dezembro)\b", q
    ):
        d, month_name = m.groups()
        mo = _MONTHS_PT[month_name]
        dates.append(f"{year:04d}-{mo:02d}-{int(d):02d}")

    seen = set()
    out = []
    for d in dates:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# 3. Adaptive retrieval SQL — matches rag_chunks / rag_documents schema
# ---------------------------------------------------------------------------

RECENCY_WINDOW_DAYS_DEFAULT = 14  # "last N days" window for current_state


def build_retrieval_sql(
    query_type: QueryType,
    dates: list[str] | None = None,
    recency_window_days: int = RECENCY_WINDOW_DAYS_DEFAULT,
    top_k: int = 5,
):
    """
    Returns (sql, params, needs_embedding).

    - sql is parameterized for asyncpg ($1, $2, ...)
    - if needs_embedding is True, $1 must be the query embedding (str form,
      same as the existing query() function: str(q_embedding))
    - params is the list of additional params AFTER the embedding, in order

    Caller usage:
        sql, extra_params, needs_emb = build_retrieval_sql(qtype, dates)
        if needs_emb:
            args = [str(q_embedding)] + extra_params
        else:
            args = extra_params
        rows = await conn.fetch(sql, *args)
    """

    if query_type == "specific_date" and dates:
        date_objs = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]
        sql = """
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   0.9 AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            WHERE rd.doc_type = 'bulletin'
              AND rd.doc_date = ANY($1::date[])
            ORDER BY rd.doc_date DESC
        """
        return sql, [date_objs], False

    if query_type == "comparison" and dates and len(dates) >= 2:
        date_objs = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]
        sql = """
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   0.9 AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            WHERE rd.doc_type = 'bulletin'
              AND rd.doc_date = ANY($1::date[])
            ORDER BY rd.doc_date ASC
        """
        return sql, [date_objs], False

    if query_type == "current_state":
        # Similarity search restricted to the most recent N days.
        # Falls back to no date filter if nothing matches (handled by caller).
        sql = f"""
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   1 - (rc.embedding <=> $1::vector) AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            WHERE rd.doc_type != 'bulletin'
               OR rd.doc_date >= (CURRENT_DATE - INTERVAL '{recency_window_days} days')
            ORDER BY similarity DESC
            LIMIT {top_k}
        """
        return sql, [], True

    if query_type == "trend":
        # Full-range similarity search with a small recency-decay bonus.
        sql = f"""
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   (1 - (rc.embedding <=> $1::vector))
                     - (COALESCE(EXTRACT(DAY FROM (CURRENT_DATE - rd.doc_date)), 0) * 0.0005)
                     AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            ORDER BY similarity DESC
            LIMIT {top_k}
        """
        return sql, [], True

    # Fallback: plain similarity search (mirrors existing behavior)
    sql = f"""
        SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
               1 - (rc.embedding <=> $1::vector) AS similarity
        FROM rag_chunks rc
        JOIN rag_documents rd ON rc.document_id = rd.id
        ORDER BY similarity DESC
        LIMIT {top_k}
    """
    return sql, [], True


async def fetch_latest_summary_chunk(conn):
    """Safety-net chunk for current_state queries: the 'quadro_resumo'
    section of the most recent bulletin, regardless of similarity ranking.

    Returns a dict shaped like the rows from build_retrieval_sql (content,
    metadata, title, doc_type, doc_date, similarity) or None.
    """
    sql = """
        SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
               1.0 AS similarity
        FROM rag_chunks rc
        JOIN rag_documents rd ON rc.document_id = rd.id
        WHERE rd.doc_type = 'bulletin'
          AND rc.metadata->>'section' = 'quadro_resumo'
        ORDER BY rd.doc_date DESC
        LIMIT 1
    """
    row = await conn.fetchrow(sql)
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# INTEGRATION NOTES for routers/rag.py
# ---------------------------------------------------------------------------
#
# Replace the body of the existing /query endpoint (the part between
# "q_embedding = vo.embed(...)" and the keyword_results fetch) with:
#
#   from time_aware_retrieval import (
#       classify_query, extract_dates, build_retrieval_sql,
#       fetch_latest_summary_chunk
#   )
#
#   qtype = classify_query(body.question)
#   dates = extract_dates(body.question) if qtype in ("specific_date", "comparison") else None
#
#   sql, extra_params, needs_embedding = build_retrieval_sql(qtype, dates)
#
#   pool = await get_pool()
#   async with pool.acquire() as conn:
#
#       if needs_embedding:
#           q_embedding = vo.embed([body.question], model="voyage-3").embeddings[0]
#           semantic_results = await conn.fetch(sql, str(q_embedding), *extra_params)
#       else:
#           semantic_results = await conn.fetch(sql, *extra_params)
#
#       # Fallback: specific_date / comparison found nothing (date not in KB)
#       if qtype in ("specific_date", "comparison") and not semantic_results:
#           qtype = "current_state"
#           sql, extra_params, needs_embedding = build_retrieval_sql(qtype)
#           q_embedding = vo.embed([body.question], model="voyage-3").embeddings[0]
#           semantic_results = await conn.fetch(sql, str(q_embedding), *extra_params)
#
#       # Safety net: always include latest quadro_resumo for current_state
#       if qtype == "current_state":
#           latest = await fetch_latest_summary_chunk(conn)
#           if latest:
#               existing = {r['content'][:100] for r in semantic_results}
#               if latest['content'][:100] not in existing:
#                   semantic_results = [latest] + list(semantic_results)
#
#       # Keyword results: unchanged from existing code
#       keyword_results = await conn.fetch("""
#           SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
#                  0.5 AS similarity
#           FROM rag_chunks rc
#           JOIN rag_documents rd ON rc.document_id = rd.id
#           WHERE to_tsvector('portuguese', rc.content)
#                 @@ plainto_tsquery('portuguese', $1)
#           LIMIT 3
#       """, body.question)
#
#   # ... rest of the function (dedup, context building, Claude call)
#   # stays exactly as-is.
#
# COST NOTE:
#   - specific_date / comparison: 0 Voyage embedding calls (vs. 1 currently)
#   - current_state / trend: 1 Voyage embedding call (same as currently)
#   - current_state adds 1 extra cheap indexed SQL query (fetch_latest_summary_chunk)
#   - No new external API calls, no new pricing tier triggers.
# ---------------------------------------------------------------------------
