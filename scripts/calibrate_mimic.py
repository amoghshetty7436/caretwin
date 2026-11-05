"""Fit patient-flow parameters from MIMIC-IV (demo or full).

Usage: python scripts/calibrate_mimic.py --mimic-dir data/raw/mimic-iv-demo [--label "MIMIC-IV v3.1"]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.sim.calibration import calibrate, save  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mimic-dir", type=Path, default=ROOT / "data/external/mimic-iv-demo-2.2")
    ap.add_argument("--label", default="MIMIC-IV Clinical Database Demo v2.2 (100 patients)")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="omit per-admission values and small counts (required for credentialed MIMIC-IV)")
    ap.add_argument("--out", type=Path, default=ROOT / "data/calibration/mimic_demo_calibration.json")
    args = ap.parse_args()
    cal = calibrate(args.mimic_dir, args.label, aggregate_only=args.aggregate_only)
    save(cal, args.out)
    print(f"{cal['n_admissions']} admissions, {cal['n_segments']} unit segments -> {args.out}")


if __name__ == "__main__":
    main()
