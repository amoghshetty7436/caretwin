"""Validate the patient-flow simulator against its calibration targets.

* occupancy vs the 85% design target (mean +/- sd over seeds)
* simulated hospital length of stay vs the MIMIC-IV demo LOS distribution
  (two-sample Kolmogorov-Smirnov on per-admission LOS in days)
* per-unit simulated segment stay vs the fitted lognormal (medians)

    python eval/validate_sim.py --seeds 10 --days 28
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.sim.engine import SimParams, Simulation  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--days", type=float, default=28)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/results/sim_validation.json")
    args = ap.parse_args()
    cal = json.loads(Path(get_settings().calibration_path).read_text())  # CARETWIN_CALIBRATION_PATH
    v = cal["validation"]
    # aggregate-only calibrations (full MIMIC-IV) carry percentiles instead of per-admission values
    mimic_los = np.array(v.get("los_days_sample") or v["los_days_quantiles"])

    occ, los_all, boarding_p90, by_unit = [], [], [], {}
    for seed in range(args.seeds):
        sim = Simulation(SimParams(days=args.days, seed=1000 + seed))
        k = sim.run()["kpis"]
        occ.append(k["occupancy"])
        boarding_p90.append(k["ed_boarding_h"]["p90"])
        for u, v in k["occupancy_by_unit"].items():
            by_unit.setdefault(u, []).append(v)
        warm = sim.p.warmup_days * 24
        los_all += [(p.discharged - p.arrival) / 24 for p in sim.patients
                    if p.pathway != "prefill" and p.arrival >= warm and p.discharged is not None]
    los = np.array(los_all)
    # Right-censoring: stays still open at the end are excluded, which biases simulated LOS low;
    # compare on the overlapping range (<= 21 days) to limit that effect.
    ks_all = stats.ks_2samp(los, mimic_los)
    ks_trunc = stats.ks_2samp(los[los <= 21], mimic_los[mimic_los <= 21])
    report = {
        "seeds": args.seeds, "days_per_seed": args.days,
        "occupancy": {"target": 0.85, "mean": round(float(np.mean(occ)), 4), "sd": round(float(np.std(occ)), 4),
                      "abs_error": round(abs(float(np.mean(occ)) - 0.85), 4)},
        "occupancy_by_unit_mean": {u: round(float(np.mean(v)), 4) for u, v in by_unit.items()},
        "los_days": {
            "simulated": {"n": int(len(los)), "mean": round(float(los.mean()), 3), "median": round(float(np.median(los)), 3),
                          "p90": round(float(np.percentile(los, 90)), 3)},
            "mimic_demo": {"n": int(len(mimic_los)), "mean": round(float(mimic_los.mean()), 3),
                           "median": round(float(np.median(mimic_los)), 3),
                           "p90": round(float(np.percentile(mimic_los, 90)), 3)},
            "ks_statistic": round(float(ks_all.statistic), 4), "ks_pvalue": round(float(ks_all.pvalue), 5),
            "ks_statistic_le_21d": round(float(ks_trunc.statistic), 4),
            "ks_pvalue_le_21d": round(float(ks_trunc.pvalue), 5),
        },
        "ed_boarding_p90_h_mean": round(float(np.mean(boarding_p90)), 2),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
