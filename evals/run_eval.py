"""
run_eval.py — Utanya RAG evaluation harness

Runs every question in eval_questions.json against the live /rag/query
endpoint, scores each result, and writes a report (console + JSON + CSV).

Scoring per question:
  PASS          — all must_contain present AND no must_not_contain present
                  AND (if query_type set) classification matches
  PARTIAL       — some but not all must_contain present, OR classification
                  mismatch but content otherwise ok
  FAIL          — no must_contain present, or a must_not_contain appeared
                  (a must_not_contain hit is always a FAIL — likely hallucination)
  ERROR         — request failed / non-200

Number/string matching is normalization-tolerant: it strips spaces, dots,
and commas used as separators so "15 888 436 377,12", "15.888.436.377,12"
and "15888436377" all match. Percentages and AOA are handled.

Usage:
    python run_eval.py
    python run_eval.py --questions eval_questions.json --out report

Set API_KEY and BASE_URL below or via environment variables.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

BASE_URL = os.environ.get(
    "UTANYA_BASE_URL",
    "https://utanya-api-production.up.railway.app"
)
API_KEY = os.environ.get("UTANYA_API_KEY", "2026Utanya232811!")
QUERY_PATH = "/rag/query"


# ---------------------------------------------------------------------------
# Normalization for tolerant matching
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase and collapse number-separator noise so figures match
    regardless of formatting. Keeps digits and letters; treats spaces,
    dots and commas between digits as removable separators."""
    t = text.lower()
    # Remove thousands/decimal separators *between digits* so
    # "15 888 436 377,12" -> "1588843637712"
    t = re.sub(r"(?<=\d)[ .,](?=\d)", "", t)
    # Collapse remaining whitespace
    t = re.sub(r"\s+", " ", t)
    return t


def contains(haystack_norm: str, needle: str) -> bool:
    return normalize(needle) in haystack_norm


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def query_api(question: str, timeout: int = 60) -> dict:
    url = BASE_URL.rstrip("/") + QUERY_PATH
    payload = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_question(item: dict, api_response: dict) -> dict:
    answer = api_response.get("answer", "") or ""
    answer_norm = normalize(answer)
    returned_qtype = api_response.get("query_type")

    must = item.get("must_contain", []) or []
    must_not = item.get("must_not_contain", []) or []
    expected_qtype = item.get("query_type")
    needs_gold = item.get("needs_gold", False)

    hits = [m for m in must if contains(answer_norm, m)]
    misses = [m for m in must if not contains(answer_norm, m)]
    violations = [m for m in must_not if contains(answer_norm, m)]

    qtype_ok = (expected_qtype is None) or (returned_qtype == expected_qtype)

    # Determine verdict
    if needs_gold:
        # Gold answer not yet filled in by the user -> needs manual gold first
        verdict = "NEEDS_GOLD"
    elif violations:
        verdict = "FAIL"  # hallucination / wrong value present — always fail
    elif not must:
        # No assertions defined (e.g. open analysis question) -> manual review
        verdict = "REVIEW"
    elif len(hits) == len(must) and qtype_ok:
        verdict = "PASS"
    elif len(hits) == 0:
        verdict = "FAIL"
    else:
        verdict = "PARTIAL"

    return {
        "id": item["id"],
        "category": item.get("category", ""),
        "question": item["question"],
        "verdict": verdict,
        "expected_qtype": expected_qtype,
        "returned_qtype": returned_qtype,
        "qtype_ok": qtype_ok,
        "hits": hits,
        "misses": misses,
        "violations": violations,
        "answer": answer,
        "expected": item.get("expected", ""),
        "needs_gold": needs_gold,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval_questions.json")
    ap.add_argument("--out", default="eval_report")
    ap.add_argument("--sleep", type=float, default=7.0,
                    help="seconds between requests (rate-limit safety; query limit is 10/min)")
    args = ap.parse_args()

    with open(args.questions, "r", encoding="utf-8") as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data

    results = []
    print(f"Running {len(questions)} questions against {BASE_URL}{QUERY_PATH}\n")

    for i, item in enumerate(questions, 1):
        qid = item["id"]
        try:
            resp = query_api(item["question"])
            res = score_question(item, resp)
        except urllib.error.HTTPError as e:
            res = {
                "id": qid, "category": item.get("category", ""),
                "question": item["question"], "verdict": "ERROR",
                "expected_qtype": item.get("query_type"), "returned_qtype": None,
                "qtype_ok": False, "hits": [], "misses": item.get("must_contain", []),
                "violations": [], "answer": f"HTTPError {e.code}: {e.read().decode('utf-8', 'ignore')}",
                "expected": item.get("expected", ""),
            }
        except Exception as e:
            res = {
                "id": qid, "category": item.get("category", ""),
                "question": item["question"], "verdict": "ERROR",
                "expected_qtype": item.get("query_type"), "returned_qtype": None,
                "qtype_ok": False, "hits": [], "misses": item.get("must_contain", []),
                "violations": [], "answer": f"{type(e).__name__}: {e}",
                "expected": item.get("expected", ""),
            }

        results.append(res)

        mark = {
            "PASS": "PASS ", "PARTIAL": "PART ", "FAIL": "FAIL ",
            "ERROR": "ERR  ", "REVIEW": "REV  ", "NEEDS_GOLD": "GOLD "
        }.get(res["verdict"], "?    ")
        qtype_note = ""
        if res["expected_qtype"] and not res["qtype_ok"]:
            qtype_note = f"  [qtype: got {res['returned_qtype']}, want {res['expected_qtype']}]"
        print(f"[{i:2}/{len(questions)}] {mark} {qid}  {res['verdict']}{qtype_note}")
        if res["violations"]:
            print(f"         !! VIOLATION (possible hallucination): {res['violations']}")
        if res["verdict"] in ("PARTIAL", "FAIL") and res["misses"]:
            print(f"         missing: {res['misses']}")

        if i < len(questions):
            time.sleep(args.sleep)

    # ---- Summary ----
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    total = len(results)
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    for v in ["PASS", "PARTIAL", "FAIL", "ERROR", "REVIEW", "NEEDS_GOLD"]:
        c = counts.get(v, 0)
        pct = (c / total * 100) if total else 0
        print(f"  {v:10} {c:3}  ({pct:.0f}%)")
    print(f"  {'TOTAL':10} {total:3}")

    scored = counts.get("PASS", 0) + counts.get("PARTIAL", 0) + counts.get("FAIL", 0)
    if scored:
        pass_rate = counts.get("PASS", 0) / scored * 100
        print(f"\n  Pass rate (excl. REVIEW/NEEDS_GOLD/ERROR): {pass_rate:.0f}%")

    # ---- Write JSON ----
    json_path = f"{args.out}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"summary": counts, "results": results}, f,
                  ensure_ascii=False, indent=2)

    # ---- Write CSV ----
    csv_path = f"{args.out}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "verdict", "expected_qtype",
                    "returned_qtype", "misses", "violations", "question", "answer"])
        for r in results:
            w.writerow([
                r["id"], r["category"], r["verdict"], r["expected_qtype"],
                r["returned_qtype"], "; ".join(r["misses"]),
                "; ".join(r["violations"]), r["question"],
                r["answer"].replace("\n", " ")
            ])

    print(f"\nReports written: {json_path} , {csv_path}")
    print("Review PARTIAL/REVIEW rows manually — REVIEW rows have no")
    print("assertions and need a human verdict.\n")

    # Non-zero exit if any FAIL/ERROR, useful for CI later
    if counts.get("FAIL", 0) or counts.get("ERROR", 0):
        sys.exit(1)


if __name__ == "__main__":
    main()
