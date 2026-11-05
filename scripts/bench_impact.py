"""Benchmark asset/infrastructure failure-impact queries against a seeded twin.

Targets every Infrastructure node plus a random sample of assets, warms up, then times
each call of ``queries.failure_impact`` (Cypher traversal + substitute lookup + Python
post-processing) end to end from the client.

    python scripts/bench_impact.py --assets 200 --rounds 3
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.neo4j import get_driver  # noqa: E402
from app.graph import queries as q  # noqa: E402


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", type=int, default=200)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/results/impact_benchmark.json")
    args = ap.parse_args()
    d = get_driver()
    infra = [r["id"] for r in q._rows(d, "MATCH (n:Infrastructure) RETURN n.id AS id ORDER BY id")]
    assets = [r["id"] for r in q._rows(d, "MATCH (n:Asset) RETURN n.id AS id ORDER BY id")]
    rng = random.Random(args.seed)
    targets = infra + rng.sample(assets, args.assets)
    for t in targets[:50]:  # warm-up: page cache + query plan cache
        q.failure_impact(d, t)
    lat, by_kind = [], {"infrastructure": [], "asset": []}
    for _ in range(args.rounds):
        rng.shuffle(targets)
        for t in targets:
            s = time.perf_counter()
            q.failure_impact(d, t)
            ms = (time.perf_counter() - s) * 1000
            lat.append(ms)
            by_kind["infrastructure" if t in infra else "asset"].append(ms)
    report = {
        "queries": len(lat), "targets": len(targets), "infrastructure_targets": len(infra),
        "asset_targets": args.assets, "rounds": args.rounds,
        "p50_ms": round(pct(lat, 0.50), 2), "p95_ms": round(pct(lat, 0.95), 2), "p99_ms": round(pct(lat, 0.99), 2),
        "mean_ms": round(statistics.mean(lat), 2), "max_ms": round(max(lat), 2),
        "p95_by_target": {k: round(pct(v, 0.95), 2) for k, v in by_kind.items()},
        "graph": q.summary(d) | {"by_label": None},
        "note": "single client, sequential, warm cache; Neo4j 5.26 community in Docker on the same 4-vCPU host",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "graph"}, indent=2))


if __name__ == "__main__":
    main()
