"""Fit patient-flow parameters from MIMIC-IV transfers.

Works with the open MIMIC-IV Clinical Database Demo (100 patients, ODbL) or, for
credentialed users, the full MIMIC-IV ``hosp`` module: the tables are identical.

Outputs a JSON document with:
  * ``unit_stay_hours``  lognormal(mu, sigma) per unit type, partially pooled for small n
  * ``ed_boarding_hours`` lognormal for ED time before an inpatient bed
  * ``transitions``      P(next unit | current unit), including DISCHARGE
  * ``entry``            first inpatient unit by pathway (ED vs direct)
  * ``arrival_hour``     24-bin admission-time profile
  * ``validation``       empirical hospital LOS summary, used to check the simulator
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

UNIT_TYPES = ["ICU", "STEPDOWN", "CARD", "MED", "NEURO", "ONC", "SURG", "TRANSPLANT", "PSYCH"]
DISCHARGE = "DISCHARGE"


def map_careunit(name: str | float) -> str | None:
    """Map a MIMIC-IV careunit string to a CareTwin unit type."""
    if not isinstance(name, str) or not name.strip() or name == "Unknown":
        return None
    n = name.lower()
    if n.startswith("emergency"):
        return "ED"
    if "discharge lounge" in n:
        return DISCHARGE
    if n == "pacu":
        return "PACU"
    if "intensive care" in n or "(ccu)" in n or "sicu" in n or "micu" in n:
        return "ICU"
    if "intermediate" in n or "stepdown" in n:
        return "STEPDOWN"
    if "transplant" in n:
        return "TRANSPLANT"
    if "psych" in n:
        return "PSYCH"
    if "oncology" in n:
        return "ONC"
    if "neuro" in n:
        return "NEURO"
    if "cardi" in n:
        return "CARD"
    if any(k in n for k in ("surg", "trauma", "vascular", "gyn", "ortho")):
        return "SURG"
    if "medicine" in n or "observation" in n:
        return "MED"
    return "MED"


def _lognormal_fit(hours: np.ndarray) -> dict:
    hours = hours[np.isfinite(hours) & (hours > 0.05)]
    logs = np.log(hours)
    return {"mu": float(logs.mean()), "sigma": float(max(logs.std(ddof=1) if len(logs) > 1 else 0.8, 0.25)),
            "n": int(len(hours)), "mean_h": float(hours.mean()), "median_h": float(np.median(hours)),
            "p90_h": float(np.percentile(hours, 90))}


def _shrink(fit: dict, pooled: dict, k: float = 15.0) -> dict:
    """Partial pooling of mu/sigma toward the pooled fit: weight n/(n+k)."""
    w = fit["n"] / (fit["n"] + k)
    out = dict(fit)
    out["mu"] = w * fit["mu"] + (1 - w) * pooled["mu"]
    out["sigma"] = w * fit["sigma"] + (1 - w) * pooled["sigma"]
    out["pooling_weight"] = round(w, 3)
    out["model_mean_h"] = math.exp(out["mu"] + out["sigma"] ** 2 / 2)
    return out


def build_segments(transfers: pd.DataFrame) -> pd.DataFrame:
    """Collapse consecutive same-unit transfer rows into unit *segments* per admission."""
    t = transfers.dropna(subset=["hadm_id"]).copy()
    t = t[t.eventtype != "discharge"]
    t["unit"] = t["careunit"].map(map_careunit)
    t = t.dropna(subset=["unit", "intime", "outtime"]).sort_values(["hadm_id", "intime"])
    t["hours"] = (t.outtime - t.intime).dt.total_seconds() / 3600
    segs = []
    for hadm, grp in t.groupby("hadm_id", sort=False):
        cur = None
        for row in grp.itertuples():
            if cur and cur["unit"] == row.unit:
                cur["hours"] += row.hours
                cur["outtime"] = row.outtime
            else:
                if cur:
                    segs.append(cur)
                cur = {"hadm_id": hadm, "unit": row.unit, "hours": row.hours, "intime": row.intime,
                       "outtime": row.outtime}
        if cur:
            segs.append(cur)
    df = pd.DataFrame(segs)
    df["seq"] = df.groupby("hadm_id").cumcount()
    df["next_unit"] = df.groupby("hadm_id")["unit"].shift(-1).fillna(DISCHARGE)
    return df


def calibrate(mimic_dir: Path, source_label: str = "MIMIC-IV Clinical Database Demo v2.2",
              aggregate_only: bool = False) -> dict:
    """``aggregate_only`` drops per-admission values and small transition counts so the output
    can be shared under the credentialed MIMIC-IV data use agreement."""
    hosp = mimic_dir / "hosp"
    adm = pd.read_csv(hosp / "admissions.csv.gz", parse_dates=["admittime", "dischtime", "edregtime", "edouttime"])
    tr = pd.read_csv(hosp / "transfers.csv.gz", parse_dates=["intime", "outtime"])
    segs = build_segments(tr)

    inpatient = segs[segs.unit.isin(UNIT_TYPES)]
    pooled = _lognormal_fit(inpatient[inpatient.unit != "ICU"].hours.to_numpy())
    stays = {}
    for u in UNIT_TYPES:
        fit = _lognormal_fit(inpatient[inpatient.unit == u].hours.to_numpy()) if (inpatient.unit == u).sum() >= 2 \
            else dict(pooled, n=0)
        stays[u] = _shrink(fit, pooled)
    pacu = segs[segs.unit == "PACU"]
    stays["PACU"] = _shrink(_lognormal_fit(pacu.hours.to_numpy()), _lognormal_fit(np.array([2.0, 3.0, 4.0, 6.0])))
    ed = segs[segs.unit == "ED"]
    ed_fit = _lognormal_fit(ed.hours.to_numpy())

    # transitions between unit types (ED -> first unit is the "entry" distribution)
    states = ["ED", "PACU"] + UNIT_TYPES
    targets = UNIT_TYPES + ["PACU", DISCHARGE]
    trans_counts = (segs[segs.unit.isin(states)].assign(next_unit=lambda d: d.next_unit.where(
        d.next_unit.isin(targets), DISCHARGE)).groupby(["unit", "next_unit"]).size())
    marginal = segs[segs.unit.isin(UNIT_TYPES)].unit.value_counts(normalize=True)
    transitions, counts_out = {}, {}
    for s in states:
        row = trans_counts.get(s, pd.Series(dtype=float))
        if s == "ED":
            row = row.drop(labels=[DISCHARGE], errors="ignore")  # all demo patients are admitted
        prior = {t: float(marginal.get(t, 0.0)) for t in UNIT_TYPES}
        prior[DISCHARGE] = 0.0 if s == "ED" else 1.0
        if s == "ED":
            prior["PACU"] = 0.0
        total_prior = sum(prior.values())
        alpha = 1.0
        n = float(row.sum())
        probs = {t: (float(row.get(t, 0.0)) + alpha * prior.get(t, 0.0) / total_prior) / (n + alpha)
                 for t in targets if (float(row.get(t, 0.0)) > 0 or prior.get(t, 0.0) > 0)}
        z = sum(probs.values())
        transitions[s] = {t: round(p / z, 5) for t, p in sorted(probs.items(), key=lambda kv: -kv[1])}
        counts_out[s] = {t: int(c) for t, c in row.items()}

    first_seg = segs[segs.seq == 0].set_index("hadm_id").unit
    first_inpatient = segs[segs.unit.isin(UNIT_TYPES + ["PACU"])].groupby("hadm_id").unit.first()
    via_ed = first_seg.eq("ED")
    entry_direct = first_inpatient[~first_inpatient.index.isin(via_ed[via_ed].index)].value_counts(normalize=True)

    adm["los_days"] = (adm.dischtime - adm.admittime).dt.total_seconds() / 86400
    arrivals = adm.edregtime.fillna(adm.admittime)
    hour_profile = arrivals.dt.hour.value_counts(normalize=True).reindex(range(24), fill_value=0.0)
    hour_profile = (hour_profile * 0.9 + 0.1 / 24)  # light smoothing for a 100-patient sample

    out = {
        "source": source_label,
        "n_admissions": int(adm.hadm_id.nunique()),
        "n_patients": int(adm.subject_id.nunique()),
        "n_segments": int(len(segs)),
        "unit_stay_hours": stays,
        "ed_boarding_hours": ed_fit,
        "transitions": transitions,
        "transition_counts": counts_out,
        "entry": {"share_via_ed": round(float(via_ed.mean()), 4),
                  "direct": {k: round(float(v), 4) for k, v in entry_direct.items()},
                  "from_ed": transitions["ED"]},
        "arrival_hour": [round(float(x), 5) for x in hour_profile.to_numpy()],
        "validation": {
            "hospital_los_days": {"mean": float(adm.los_days.mean()), "median": float(adm.los_days.median()),
                                  "p90": float(adm.los_days.quantile(0.9)), "n": int(adm.los_days.notna().sum())},
            "segments_per_admission": float(segs.groupby("hadm_id").size().mean()),
            "icu_share": float(segs.groupby("hadm_id").unit.apply(lambda u: (u == "ICU").any()).mean()),
            "los_days_quantiles": [round(float(adm.los_days.quantile(q / 100)), 4) for q in range(1, 100)],
            **({} if aggregate_only else
               {"los_days_sample": [round(float(x), 4) for x in adm.los_days.dropna().sort_values()]}),
        },
        "aggregate_only": aggregate_only,
    }
    if aggregate_only:  # no cells under 10 (common small-cell suppression rule)
        out["transition_counts"] = {s: {t: c for t, c in row.items() if c >= 10}
                                    for s, row in out["transition_counts"].items()}
    return out


def save(cal: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal, indent=2))


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text())
