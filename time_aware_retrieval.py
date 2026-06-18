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
    " vs ", " versus ", "comparado com", "comparada com", "em relação a", "em relacao a",
    "diferença entre", "diferenca entre", "comparar", "compare", "comparação", "comparacao"
]


def classify_query(question: str) -> QueryType:
    """Classify a user question into a retrieval strategy bucket.

    Order: comparison > specific_date > current_state > trend > default.
    """
    q = question.lower()

    date_matches = []
    for pattern in _DATE_PATTERNS:
        date_matches.extend(re.findall(pattern, q))

    # "X e Y de <month>" packs two dates the single-date pattern counts once.
    two_days_one_month = re.search(
        r"\b\d{1,2}\s+e\s+\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|marco|abril|"
        r"maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", q
    )
    # "entre X e Y" framing signals a two-point comparison/evolution.
    entre_framing = bool(re.search(r"\bentre\b.+\be\b", q))

    has_comparison_kw = any(kw in q for kw in _COMPARISON_KEYWORDS)
    has_multiple_dates = len(date_matches) >= 2 or two_days_one_month is not None

    # "diferença entre" / "comparado com" only mean a *temporal* comparison
    # when dates are present; otherwise they're conceptual ("diferença entre
    # BT e OT-NR") and should fall through to current_state.
    comparison_needs_dates = {"diferença entre", "diferenca entre",
                              "comparado com", "comparada com",
                              "em relação a", "em relacao a"}
    if has_comparison_kw and not date_matches:
        if all(kw in comparison_needs_dates for kw in _COMPARISON_KEYWORDS if kw in q):
            has_comparison_kw = False

    if has_comparison_kw or has_multiple_dates or (entre_framing and date_matches):
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

    # "22 e 29 de maio" -> two dates sharing the same trailing month
    # (must run before the single "X de mes" pattern below, and consume
    # both numbers so the single-date pattern doesn't also match the
    # second number on its own)
    consumed_spans = []
    for m in re.finditer(
        r"\b(\d{1,2})\s+e\s+(\d{1,2})\s+de\s+(janeiro|fevereiro|março|marco|abril|maio|"
        r"junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", q
    ):
        d1, d2, month_name = m.groups()
        mo = _MONTHS_PT[month_name]
        dates.append(f"{year:04d}-{mo:02d}-{int(d1):02d}")
        dates.append(f"{year:04d}-{mo:02d}-{int(d2):02d}")
        consumed_spans.append(m.span())

    def _in_consumed(pos):
        return any(start <= pos < end for start, end in consumed_spans)

    for m in re.finditer(
        r"\b(\d{1,2})\s+de\s+(janeiro|fevereiro|março|marco|abril|maio|junho|"
        r"julho|agosto|setembro|outubro|novembro|dezembro)\b", q
    ):
        if _in_consumed(m.start()):
            continue
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


# ---------------------------------------------------------------------------
# Section-intent detection: figure out which bulletin section a question
# is about, so date-anchored retrieval can prioritize the right chunk.
# Section names must match the metadata->>'section' values produced by
# bulletin_json_converter.py (e.g. quadro_resumo, desempenho_membros,
# otnr_bond, otnr_summary, yield_curve_kz, yield_curve_otx, repos,
# corporate_bonds, stock, stocks_summary, otme_exchange, otc_otnr,
# primary_market, eventos / eventos_distribuicao).
# ---------------------------------------------------------------------------

_SECTION_KEYWORDS = {
    "yield_curve_kz": ["curva de rendimento", "curva kz", "yield curve",
                       "ponto 3m", "ponto 6m", "ponto 1y", "ponto 2y",
                       "ponto 3y", "ponto 4y", "ponto 5y", "ponto 6y",
                       "ponto 7y", "ponto 8y", "ponto 9y", "ponto 10y",
                       "taxa de rendimento do ponto", "estrutura de prazo"],
    "yield_curve_otx": ["curva ot-tx", "curva otx", "ot-tx"],
    "repos": ["reporte", "repo", "recompra", "taxa repo", "haircut", "colateral"],
    "primary_market": ["leilão", "leilao", "mercado primário", "mercado primario",
                       "emissão", "emissao", "subscrição", "subscricao",
                       "montante colocado", "montante ofertado", "competitivo"],
    "eventos": ["evento de distribuição", "evento de distribuicao", "cupão",
                "cupao", "resgate", "maturidade", "isin", "distribuição de rendimentos",
                "distribuicao de rendimentos"],
    "corporate_bonds": ["obrigação corporativa", "obrigacao corporativa",
                        "obrigações privadas", "obrigacoes privadas",
                        "snledofb", "baiodofa", "snl", "obrigação privada"],
    "stock": ["acção", "accao", "acções", "accoes", "capitalização", "capitalizacao",
              "bolsista", "bai", "bfa", "bcga", "ensa", "bdva", "cotação da acção"],
    "stocks_summary": ["total de acções", "total accoes", "mercado de acções"],
    "desempenho_membros": ["membro", "membros", "desempenho", "negociou",
                           "maior montante", "quota de mercado", "ranking",
                           "número de negócios", "numero de negocios"],
    "otme_exchange": ["ot-me", "moeda externa", "ot me"],
    "otnr_bond": ["ot-nr", "obrigação do tesouro", "obrigacao do tesouro",
                  "ytm", "cupão da", "variação da", "variacao da", "cotação da"],
    "otnr_summary": ["total de ot-nr", "negócios de ot-nr", "negocios de ot-nr",
                     "volume de ot-nr"],
    "quadro_resumo": ["montante total", "total negociado", "total da sessão",
                      "total da sessao", "resumo", "volume total da sessão"],
}


def detect_sections(question: str) -> list[str]:
    """Return an ordered list of section names the question most likely
    targets (most specific first). Used to prioritize chunk selection for
    date-anchored queries. An instrument code like 'OG13M29A' strongly
    implies otnr_bond/otme/corporate sections."""
    q = question.lower()
    scored = []
    for section, kws in _SECTION_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in q)
        if hits:
            scored.append((hits, section))
    # Instrument-code pattern (e.g. OG13M29A, EL13G33A, SNLEDOFB) -> bond rows
    if re.search(r"\b[A-Z]{2}\d{2}[A-Z]\d{2}[A-Z]\b", question) or \
       re.search(r"\b[A-Z]{6,8}\b", question):
        scored.append((1, "otnr_bond"))
        scored.append((1, "corporate_bonds"))
    scored.sort(reverse=True)
    # De-dup preserving order
    seen = set()
    out = []
    for _, s in scored:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _section_priority_sql(sections: list[str]) -> str:
    """Build an ORDER BY fragment that floats the detected sections to the
    top, then quadro_resumo as a sensible default, then by id."""
    clauses = []
    for s in sections:
        safe = s.replace("'", "")
        clauses.append(f"(rc.metadata->>'section' = '{safe}') DESC")
    # Always keep quadro_resumo near the top as a fallback context anchor
    clauses.append("(rc.metadata->>'section' = 'quadro_resumo') DESC")
    clauses.append("rc.id")
    return ",\n                               ".join(clauses)


def build_retrieval_sql(
    query_type: QueryType,
    dates: list[str] | None = None,
    recency_window_days: int = RECENCY_WINDOW_DAYS_DEFAULT,
    top_k: int = 15,
    question: str = "",
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
        # Single (or few) dates: be generous. A bulletin has ~12 section
        # types and the user may ask about any of them, so return enough
        # chunks that the targeted section always survives. Section-intent
        # detection floats the relevant section to the top.
        sections = detect_sections(question)
        order_by = _section_priority_sql(sections)
        per_date_limit = 12
        sql = f"""
            SELECT content, metadata, title, doc_type, doc_date, similarity
            FROM (
                SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                       0.9 AS similarity,
                       ROW_NUMBER() OVER (
                           PARTITION BY rd.doc_date
                           ORDER BY {order_by}
                       ) AS rn
                FROM rag_chunks rc
                JOIN rag_documents rd ON rc.document_id = rd.id
                WHERE rd.doc_type = 'bulletin'
                  AND rd.doc_date = ANY($1::date[])
            ) ranked
            WHERE rn <= {per_date_limit}
            ORDER BY doc_date DESC, rn ASC
        """
        return sql, [date_objs], False

    if query_type == "comparison" and dates and len(dates) >= 2:
        date_objs = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]
        # Multiple dates: cap per date to keep balance, but use section
        # detection so the compared metric (e.g. yield curve point, stock
        # cap) is the chunk that survives for EACH date. 6 per date covers
        # the targeted section + summary + a margin.
        sections = detect_sections(question)
        order_by = _section_priority_sql(sections)
        per_date_limit = 6
        sql = f"""
            SELECT content, metadata, title, doc_type, doc_date, similarity
            FROM (
                SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                       0.9 AS similarity,
                       ROW_NUMBER() OVER (
                           PARTITION BY rd.doc_date
                           ORDER BY {order_by}
                       ) AS rn
                FROM rag_chunks rc
                JOIN rag_documents rd ON rc.document_id = rd.id
                WHERE rd.doc_type = 'bulletin'
                  AND rd.doc_date = ANY($1::date[])
            ) ranked
            WHERE rn <= {per_date_limit}
            ORDER BY doc_date ASC, rn ASC
        """
        return sql, [date_objs], False

    if query_type == "current_state":
        # Similarity search restricted to the most recent N days.
        # Section-detection bonus floats the targeted chunk type to the top.
        # Falls back to no date filter if nothing matches (handled by caller).
        sections = detect_sections(question) if question else []
        section_bonus = ""
        if sections:
            cases = " + ".join(
                f"(CASE WHEN rc.metadata->>'section' = '{s.replace(chr(39), '')}'"
                f" THEN {0.08 - i * 0.01:.2f} ELSE 0 END)"
                for i, s in enumerate(sections[:4])
            )
            section_bonus = f"+ {cases}"
        sql = f"""
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   (1 - (rc.embedding <=> $1::vector)) {section_bonus} AS similarity
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
            WHERE rd.doc_type != 'bulletin'
               OR rd.doc_date >= (CURRENT_DATE - INTERVAL '{recency_window_days} days')
            ORDER BY similarity DESC
            LIMIT {top_k}
        """
        return sql, [], True

    if query_type == "trend":
        # Full-range similarity search across all history.
        # Small recency-decay keeps recent data slightly preferred when scores tie.
        # Section bonus surfaces the right chunk type (e.g. yield_curve_kz for
        # "evolução da curva de rendimento") across all bulletins.
        sections = detect_sections(question) if question else []
        section_bonus = ""
        if sections:
            cases = " + ".join(
                f"(CASE WHEN rc.metadata->>'section' = '{s.replace(chr(39), '')}'"
                f" THEN {0.08 - i * 0.01:.2f} ELSE 0 END)"
                for i, s in enumerate(sections[:4])
            )
            section_bonus = f"+ {cases}"
        sql = f"""
            SELECT rc.content, rc.metadata, rd.title, rd.doc_type, rd.doc_date,
                   (1 - (rc.embedding <=> $1::vector))
                     - (COALESCE(CURRENT_DATE - rd.doc_date, 0) * 0.0005)
                     {section_bonus}
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
