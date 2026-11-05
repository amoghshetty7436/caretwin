"""Score the assistant on eval/questions.jsonl.

A question is correct when the answer contains every expected fact (whole-token match,
case-insensitive), contains at least one ``any_of`` fact when given, and the answer's
citations verified as grounded in that turn's tool outputs.

    python eval/run_eval.py                      # offline planner (no API key)
    CARETWIN_LLM_PROVIDER=openai_compat CARETWIN_LLM_API_KEY=... python eval/run_eval.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.assistant.agent import get_assistant  # noqa: E402
from app.db.neo4j import get_driver  # noqa: E402


def contains(answer: str, fact: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(fact.lower())}(?![\w-])", answer.lower()) is not None


def grade(q: dict, res: dict) -> dict:
    ans = res["answer"]
    missing = [e for e in q["expected"] if not contains(ans, e)]
    any_ok = not q["any_of"] or any(contains(ans, a) for a in q["any_of"])
    ok = not missing and any_ok and res["grounded"]
    return {"id": q["id"], "category": q["category"], "question": q["question"], "correct": ok,
            "missing": missing, "any_of_ok": any_ok, "grounded": res["grounded"], "answer": ans,
            "citations": res["citations"], "latency_ms": round(res["latency_ms"], 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=ROOT / "eval/questions.jsonl")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    lines = [json.loads(line) for line in args.questions.read_text().splitlines() if line.strip()]
    meta, questions = lines[0]["_meta"], lines[1:]
    d = get_driver()
    live = d.execute_query("MATCH (p:Patient) RETURN count(p) AS c").records[0]["c"]
    if live != meta["census_patients"]:
        sys.exit(f"census mismatch: gold built on {meta['census_patients']} patients, DB has {live}. "
                 "Re-seed with the default seeds (python -m app.seed).")
    assistant = get_assistant()
    provider = getattr(assistant, "provider", None) or assistant.s.llm_model
    rows = [grade(q, assistant.ask(d, q["question"]).to_dict()) for q in questions]
    by_cat: dict[str, list[int]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(int(r["correct"]))
    report = {"provider": provider, "correct": sum(r["correct"] for r in rows), "total": len(rows),
              "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 4),
              "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in by_cat.items()},
              "grounded_rate": round(sum(r["grounded"] for r in rows) / len(rows), 4),
              "p95_latency_ms": sorted(r["latency_ms"] for r in rows)[int(len(rows) * 0.95) - 1], "results": rows}
    out = args.out or ROOT / "eval" / "results" / f"{re.sub(r'[^a-z0-9]+', '_', provider.lower())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"{provider}: {report['correct']}/{report['total']} correct ({report['accuracy']:.0%}) "
          f"{report['by_category']} grounded={report['grounded_rate']:.0%}")
    for r in rows:
        if not r["correct"]:
            print(f"  x {r['id']} {r['question']}\n      missing={r['missing']} any_of_ok={r['any_of_ok']} "
                  f"grounded={r['grounded']}\n      -> {r['answer'][:220]}")


if __name__ == "__main__":
    main()
