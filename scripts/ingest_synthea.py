"""Ingest Synthea CSV output into Postgres and write the simulator case-mix pool.

Usage: python scripts/ingest_synthea.py --csv-dir data/raw/synthea/csv [--no-db]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.sim.synthea import build_pool, ingest_postgres  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", type=Path, default=ROOT / "data/raw/synthea/csv")
    ap.add_argument("--out", type=Path, default=ROOT / "data/calibration/synthea_admissions.csv.gz")
    ap.add_argument("--no-db", action="store_true", help="only write the pool file")
    args = ap.parse_args()

    patients, enc, pool = build_pool(args.csv_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pool.to_csv(args.out, index=False, compression="gzip")
    summary = {
        "synthea_patient_records": int(len(patients)),
        "synthea_alive": int(patients.DEATHDATE.isna().sum()),
        "inpatient_admissions": int(len(pool)),
        "emergency_visits": int((enc.encounter_class if "encounter_class" in enc else enc.ENCOUNTERCLASS)
                                .eq("emergency").sum()),
        "unit_mix": pool.unit_type.value_counts(normalize=True).round(4).to_dict(),
        "pathway_mix": pool.pathway.value_counts(normalize=True).round(4).to_dict(),
        "age": pool.age.describe().round(2).to_dict(),
    }
    if not args.no_db:
        summary["postgres"] = ingest_postgres(patients, enc)
    (args.out.parent / "synthea_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
