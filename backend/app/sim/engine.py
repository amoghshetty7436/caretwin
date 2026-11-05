"""Discrete-event patient-flow simulation (SimPy).

Arrivals bootstrap the Synthea case-mix pool; ED workup, unit stays and inter-unit
transfers follow distributions fitted on MIMIC-IV. Beds are the concrete Bed nodes of
the hospital graph, so simulated patients can be written back into Neo4j as the
current census, and graph-derived bed outages (e.g. a failed oxygen zone valve) can be
replayed as what-if scenarios.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import simpy

from app.config import REPO_ROOT, get_settings
from app.sim.calibration import DISCHARGE, UNIT_TYPES

FIT_PATH = REPO_ROOT / "data" / "calibration" / "sim_fit.json"
GENERAL = {"MED", "SURG", "CARD", "NEURO", "ONC"}  # may take off-service overflow patients
MAX_SEGMENT_H = 30 * 24
PREFILL = 0.85
POOL_FALLBACK = {"STEPDOWN": ("CARD", "MED"), "NEURO": ("NEURO", "MED")}
ACUITY = {"ICU": (4, 5), "STEPDOWN": (3, 4), "TRANSPLANT": (2, 4), "CARD": (2, 3), "NEURO": (2, 3),
          "ONC": (2, 3), "SURG": (1, 3), "MED": (1, 3), "PSYCH": (1, 2)}


@dataclass
class SimParams:
    days: float = 21.0
    warmup_days: float = 7.0
    seed: int = 42
    arrivals_per_day: float | None = None          # None -> fitted value
    unit_weights: dict[str, float] | None = None   # None -> fitted values
    surge_multiplier: float = 1.0                  # scales arrivals during the surge window
    surge_start_h: float = 0.0
    surge_end_h: float = 0.0
    offline_beds: list[str] = field(default_factory=list)  # bed ids taken out of service
    outage_start_h: float = 0.0
    outage_end_h: float = 1e9
    ed_bays: int = 24
    overflow_after_h: float = 8.0
    non_elective_ed_share: float | None = None     # None -> derived from MIMIC share_via_ed
    record_census: bool = True


class BedManager:
    """Per-unit-type FIFO queues over concrete bed ids, with off-service overflow."""

    def __init__(self, env: simpy.Environment, beds_by_unit: dict[str, list[tuple[str, str]]]):
        self.env = env
        self.free: dict[str, deque] = {u: deque(bid for bid, _ in b) for u, b in beds_by_unit.items()}
        self.capacity = {u: len(b) for u, b in beds_by_unit.items()}
        self.waiting: dict[str, deque] = defaultdict(deque)
        self.occupant: dict[str, object] = {}
        self.unit_of_bed = {bid: u for u, beds in beds_by_unit.items() for bid, _ in beds}
        self.ward_of_bed = {bid: w for beds in beds_by_unit.values() for bid, w in beds}
        self.blocked: set[str] = set()
        self.blocked_free: set[str] = set()  # blocked beds that are currently empty

    def request(self, unit: str, patient) -> simpy.Event:
        ev = self.env.event()
        bed = self._take(unit)
        if bed:
            self.occupant[bed] = patient
            ev.succeed(bed)
        else:
            self.waiting[unit].append((ev, patient, self.env.now))
        return ev

    def try_overflow(self, unit: str, ev: simpy.Event, patient) -> bool:
        """Place a long-waiting general-medicine patient in another general unit's bed."""
        if ev.triggered or unit not in GENERAL:
            return False
        for other in sorted(GENERAL - {unit}, key=lambda u: (-len(self.free.get(u, ())), u)):
            bed = self._take(other)
            if bed:
                self.waiting[unit] = deque(w for w in self.waiting[unit] if w[0] is not ev)
                self.occupant[bed] = patient
                patient.off_service = True
                ev.succeed(bed)
                return True
        return False

    def _take(self, unit: str) -> str | None:
        q = self.free.get(unit)
        while q:
            bed = q.popleft()
            if bed not in self.blocked:
                return bed
            self.blocked_free.add(bed)
        return None

    def release(self, bed: str) -> None:
        self.occupant.pop(bed, None)
        if bed in self.blocked:
            self.blocked_free.add(bed)
            return
        unit = self.unit_of_bed[bed]
        q = self.waiting.get(unit)
        while q:
            ev, patient, _ = q.popleft()
            if not ev.triggered:
                self.occupant[bed] = patient
                ev.succeed(bed)
                return
        self.free[unit].append(bed)

    def block(self, beds: list[str]) -> None:
        self.blocked.update(beds)
        for u, q in self.free.items():
            keep = deque(b for b in q if b not in self.blocked)
            self.blocked_free.update(b for b in q if b in self.blocked)
            self.free[u] = keep

    def unblock(self) -> None:
        returning = sorted(self.blocked_free)
        self.blocked.clear()
        self.blocked_free = set()
        for b in returning:
            if b not in self.occupant:
                self.release(b)

    def occupied(self, unit: str) -> int:
        return sum(1 for b in self.occupant if self.unit_of_bed.get(b) == unit)


@dataclass
class Patient:
    pid: str
    rec: dict
    arrival: float
    pathway: str
    unit: str
    acuity: int = 1
    off_service: bool = False
    bed: str | None = None
    ed_wait_h: float = 0.0
    boarding_h: float = 0.0
    discharged: float | None = None
    path: list[str] = field(default_factory=list)


def load_inputs() -> tuple[dict, pd.DataFrame, dict]:
    s = get_settings()
    cal = json.loads(Path(s.calibration_path).read_text())
    pool = pd.read_csv(s.synthea_pool_path)
    fit = json.loads(FIT_PATH.read_text()) if FIT_PATH.exists() else {}
    return cal, pool, fit


def beds_by_unit_from_layout() -> dict[str, list[tuple[str, str]]]:
    """Bed ids exactly as the graph generator creates them (same seed => same rooms)."""
    from app.graph.model import generate_hospital
    g = generate_hospital(get_settings().graph_seed)
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for n in g.nodes:
        if n["label"] == "Bed" and n["inpatient"]:
            out[n["unit_type"]].append((n["id"], n["ward"]))
    return dict(out)


_LAYOUT_CACHE: dict | None = None


def _layout() -> dict:
    global _LAYOUT_CACHE
    if _LAYOUT_CACHE is None:
        _LAYOUT_CACHE = beds_by_unit_from_layout()
    return _LAYOUT_CACHE


class Simulation:
    def __init__(self, params: SimParams, cal: dict | None = None, pool: pd.DataFrame | None = None,
                 fit: dict | None = None):
        if cal is None or pool is None:
            cal, pool, fit0 = load_inputs()
            fit = fit if fit is not None else fit0
        self.p = params
        self.cal = cal
        self.fit = fit or {}
        self.rng = np.random.default_rng(params.seed)
        self.env = simpy.Environment()
        self.beds = BedManager(self.env, _layout())
        self.ed = simpy.Resource(self.env, capacity=params.ed_bays)
        self.pacu = simpy.Resource(self.env, capacity=12)
        self.patients: list[Patient] = []
        self.census: list[dict] = []
        self.unit_pools = {u: g.reset_index(drop=True) for u, g in pool.groupby("unit_type")}
        # Synthea has no step-down admissions; borrow cardiac/medical cases for that unit.
        for unit, donors in POOL_FALLBACK.items():
            if unit not in self.unit_pools or len(self.unit_pools[unit]) < 50:
                donor = pool[pool.unit_type.isin(donors)]
                self.unit_pools[unit] = pd.concat([self.unit_pools.get(unit, donor.iloc[:0]), donor]) \
                    .reset_index(drop=True)
        weights = params.unit_weights or self.fit.get("unit_weights") or {u: 1.0 for u in UNIT_TYPES}
        self.unit_weights = {u: float(weights.get(u, 0.0)) for u in UNIT_TYPES if u in self.unit_pools}
        z = sum(self.unit_weights.values())
        self.unit_probs = {u: w / z for u, w in self.unit_weights.items()}
        self.rate = params.arrivals_per_day or self.fit.get("arrivals_per_day") or 45.0
        hour = np.array(cal["arrival_hour"], dtype=float)
        self.hour_profile = hour / hour.mean()  # mean 1.0
        share_ed = cal["entry"]["share_via_ed"]
        elective_share = float((pool.pathway == "elective").mean())
        self.p_ed_nonelective = params.non_elective_ed_share or min(0.95, share_ed / max(1e-6, 1 - elective_share))
        self.transitions = cal["transitions"]
        self.icu_correction = float(self.fit.get("icu_transfer_correction", 1.0))

    # -- sampling helpers -------------------------------------------------------
    def _stay_h(self, unit: str) -> float:
        d = self.cal["unit_stay_hours"][unit]
        return float(min(MAX_SEGMENT_H, self.rng.lognormal(d["mu"], d["sigma"])))

    def _ed_workup_h(self) -> float:
        d = self.cal["ed_boarding_hours"]
        return float(min(48.0, self.rng.lognormal(d["mu"], d["sigma"])))

    def _next_unit(self, unit: str) -> str:
        probs = dict(self.transitions.get(unit, {DISCHARGE: 1.0}))
        probs.pop("PACU", None) if unit == "PACU" else None
        if unit != "ICU" and "ICU" in probs:
            probs["ICU"] *= self.icu_correction
        keys = list(probs)
        w = np.array([probs[k] for k in keys])
        return keys[int(self.rng.choice(len(keys), p=w / w.sum()))]

    def _acuity(self, unit: str, age: int) -> int:
        lo, hi = ACUITY.get(unit, (1, 3))
        a = int(self.rng.integers(lo, hi + 1)) + (1 if age >= 80 and self.rng.random() < 0.3 else 0)
        return min(5, a)

    # -- processes ---------------------------------------------------------------
    def arrivals(self):
        n = 0
        units = list(self.unit_probs)
        probs = np.array([self.unit_probs[u] for u in units])
        while True:
            t = self.env.now
            hour = int(t) % 24
            lam = self.rate / 24.0 * self.hour_profile[hour]
            if self.p.surge_start_h <= t < self.p.surge_end_h:
                lam *= self.p.surge_multiplier
            yield self.env.timeout(float(self.rng.exponential(1.0 / lam)))
            unit = units[int(self.rng.choice(len(units), p=probs))]
            pool = self.unit_pools[unit]
            rec = pool.iloc[int(self.rng.integers(len(pool)))].to_dict()
            if rec["pathway"] == "elective":
                pathway = "elective"
            else:
                pathway = "ed" if self.rng.random() < self.p_ed_nonelective else "direct"
            n += 1
            pt = Patient(pid=f"PT-{self.p.seed}-{n:05d}", rec=rec, arrival=self.env.now, pathway=pathway, unit=unit,
                         acuity=self._acuity(unit, int(rec["age"])))
            self.patients.append(pt)
            self.env.process(self.journey(pt))

    def _wait_bed(self, unit: str, pt: Patient):
        ev = self.beds.request(unit, pt)
        if not ev.triggered and unit in GENERAL:
            def overflow_timer():
                yield self.env.timeout(self.p.overflow_after_h)
                self.beds.try_overflow(unit, ev, pt)
            self.env.process(overflow_timer())
        bed = yield ev
        return bed

    def journey(self, pt: Patient):
        unit = pt.unit
        if pt.pathway == "ed":
            t0 = self.env.now
            with self.ed.request() as bay:
                yield bay
                pt.ed_wait_h = self.env.now - t0
                yield self.env.timeout(self._ed_workup_h())
                ready = self.env.now
                bed = yield from self._wait_bed(unit, pt)
                pt.boarding_h = self.env.now - ready
        elif pt.pathway == "elective" and unit in {"SURG", "TRANSPLANT"}:
            with self.pacu.request() as bay:
                yield bay
                yield self.env.timeout(float(self.rng.lognormal(math.log(2.5), 0.5)) +
                                       self._pacu_h())
                ready = self.env.now
                bed = yield from self._wait_bed(unit, pt)
                pt.boarding_h = self.env.now - ready
        else:
            ready = self.env.now
            bed = yield from self._wait_bed(unit, pt)
            pt.boarding_h = self.env.now - ready
        pt.bed = bed
        pt.path.append(unit)

        for _ in range(6):
            yield self.env.timeout(self._stay_h(unit))
            nxt = self._next_unit(unit)
            if nxt == DISCHARGE or nxt == unit:
                break
            if nxt == "PACU":  # procedure: patient leaves and returns to a unit after PACU
                yield self.env.timeout(self._pacu_h())
                nxt = self._next_unit("PACU")
                if nxt in (DISCHARGE, unit):
                    continue
            new_bed = yield from self._wait_bed(nxt, pt)  # holds current bed while waiting (blocking)
            self.beds.release(pt.bed)
            pt.bed, unit = new_bed, nxt
            pt.unit = nxt
            pt.acuity = self._acuity(nxt, int(pt.rec["age"]))
            pt.path.append(nxt)
        self.beds.release(pt.bed)
        pt.discharged = self.env.now
        pt.bed = None

    def _pacu_h(self) -> float:
        d = self.cal["unit_stay_hours"]["PACU"]
        return float(min(24.0, self.rng.lognormal(d["mu"], d["sigma"])))

    def outage(self):
        if not self.p.offline_beds:
            return
        yield self.env.timeout(self.p.outage_start_h)
        self.beds.block(self.p.offline_beds)
        displaced = [self.beds.occupant.get(b) for b in self.p.offline_beds if b in self.beds.occupant]
        for pt in displaced:  # relocate patients from failed beds to the same unit type
            if pt and pt.bed:
                self.env.process(self._relocate(pt))
        if self.p.outage_end_h < 1e8:
            yield self.env.timeout(max(0.0, self.p.outage_end_h - self.p.outage_start_h))
            self.beds.unblock()

    def _relocate(self, pt: Patient):
        old = pt.bed
        unit = self.beds.unit_of_bed[old]
        new_bed = yield from self._wait_bed(unit, pt)
        if pt.bed == old:
            self.beds.occupant.pop(old, None)
            self.beds.blocked_free.add(old)
            pt.bed = new_bed
        else:
            self.beds.release(new_bed)

    def recorder(self):
        while True:
            row = {"t_h": self.env.now}
            for u in self.beds.capacity:
                row[u] = self.beds.occupied(u)
            row["ED_boarding"] = sum(len(q) for q in self.beds.waiting.values())
            row["ED_in_bays"] = self.ed.count
            row["ED_waiting_room"] = len(self.ed.queue)
            row["offline_beds"] = len(self.beds.blocked)
            self.census.append(row)
            yield self.env.timeout(1.0)

    # -- run ---------------------------------------------------------------------
    def run(self) -> dict:
        t0 = time.perf_counter()
        self._prefill()
        self.env.process(self.arrivals())
        self.env.process(self.outage())
        if self.p.record_census:
            self.env.process(self.recorder())
        self.env.run(until=(self.p.days + self.p.warmup_days) * 24)
        return {"kpis": self.kpis(), "census": self.census_summary(), "runtime_ms": (time.perf_counter() - t0) * 1000}

    def _prefill(self):
        """Start near steady state: fill ~75% of beds with patients mid-stay so warm-up is short."""
        for unit, cap in self.beds.capacity.items():
            if unit not in self.unit_pools:
                continue
            pool = self.unit_pools[unit]
            for _ in range(int(cap * PREFILL)):
                rec = pool.iloc[int(self.rng.integers(len(pool)))].to_dict()
                pt = Patient(pid=f"PT-{self.p.seed}-P{len(self.patients):05d}", rec=rec, arrival=0.0,
                             pathway="prefill", unit=unit, acuity=self._acuity(unit, int(rec["age"])))
                self.patients.append(pt)
                self.env.process(self._prefilled(pt))

    def _prefilled(self, pt: Patient):
        bed = yield from self._wait_bed(pt.unit, pt)
        pt.bed = bed
        pt.path.append(pt.unit)
        # residual stay of a patient found mid-stay is length-biased: pick among candidate stays with p ~ length
        cand = np.array([self._stay_h(pt.unit) for _ in range(8)])
        stay = float(cand[int(self.rng.choice(8, p=cand / cand.sum()))])
        yield self.env.timeout(stay * float(self.rng.random()))
        self.beds.release(pt.bed)
        pt.discharged = self.env.now
        pt.bed = None

    def kpis(self) -> dict:
        warm = self.p.warmup_days * 24
        end = (self.p.days + self.p.warmup_days) * 24
        rows = [r for r in self.census if r["t_h"] >= warm]
        occ = {}
        for u, cap in self.beds.capacity.items():
            if rows and cap:
                occ[u] = round(float(np.mean([r[u] for r in rows])) / cap, 4)
        total_cap = sum(self.beds.capacity.values())
        hosp_occ = round(float(np.mean([sum(r[u] for u in self.beds.capacity) for r in rows])) / total_cap, 4) \
            if rows else None
        arrived = [p for p in self.patients if p.pathway != "prefill" and p.arrival >= warm]
        completed = [p for p in arrived if p.discharged is not None]
        ed = [p for p in arrived if p.pathway == "ed" and p.bed is not None or (p.pathway == "ed" and p.discharged)]
        board = np.array([p.boarding_h for p in ed]) if ed else np.array([0.0])
        los = np.array([(p.discharged - p.arrival) / 24 for p in completed]) if completed else np.array([0.0])
        return {
            "simulated_days": self.p.days,
            "arrivals": len(arrived),
            "arrivals_per_day": round(len(arrived) / self.p.days, 2),
            "discharges": len(completed),
            "occupancy": hosp_occ,
            "occupancy_by_unit": occ,
            "ed_boarding_h": {"mean": round(float(board.mean()), 2), "p50": round(float(np.median(board)), 2),
                              "p90": round(float(np.percentile(board, 90)), 2)},
            "ed_boarding_over_4h_share": round(float((board > 4).mean()), 4),
            "ed_wait_for_bay_h_p90": round(float(np.percentile([p.ed_wait_h for p in ed] or [0], 90)), 2),
            "los_days": {"mean": round(float(los.mean()), 3), "median": round(float(np.median(los)), 3),
                         "p90": round(float(np.percentile(los, 90)), 3)},
            "off_service_share": round(float(np.mean([p.off_service for p in arrived])) if arrived else 0.0, 4),
            "peak_boarding": max((r["ED_boarding"] for r in rows), default=0),
            "transfers_per_admission": round(float(np.mean([len(p.path) for p in completed])) if completed else 0, 3),
            "end_time_h": end,
        }

    def census_summary(self) -> list[dict]:
        warm = self.p.warmup_days * 24
        return [r for r in self.census if r["t_h"] >= warm and int(r["t_h"]) % 4 == 0]

    def current_patients(self) -> list[dict]:
        out = []
        for bed, pt in self.beds.occupant.items():
            if not isinstance(pt, Patient):
                continue
            out.append({"id": pt.pid, "bed_id": bed, "unit_type": self.beds.unit_of_bed[bed],
                        "ward": self.beds.ward_of_bed[bed], "acuity": pt.acuity, "age": int(pt.rec["age"]),
                        "sex": pt.rec["sex"], "reason": str(pt.rec["reason"])[:120],
                        "synthea_patient_id": pt.rec["patient_id"], "synthea_encounter_id": pt.rec["encounter_id"],
                        "pathway": pt.pathway, "off_service": pt.off_service,
                        "hours_in_hospital": round(self.env.now - pt.arrival, 1)})
        return sorted(out, key=lambda r: r["bed_id"])


def run_simulation(params: SimParams) -> tuple[dict, Simulation]:
    sim = Simulation(params)
    return sim.run(), sim


def fit_rates(target: float = 0.85, iterations: int = 12, days: float = 56.0, seed: int = 7, reps: int = 2,
              damping: float = 0.6, verbose: bool = False) -> dict:
    """Fit total arrival rate and per-unit case-mix weights so each unit type runs at ``target``
    mean occupancy (damped multiplicative updates on simulated occupancy, averaged over ``reps``
    seeds), plus an ICU-transfer correction for the ICU-enriched MIMIC demo cohort."""
    cal, pool, _ = load_inputs()
    caps = {u: len(v) for u, v in _layout().items()}
    weights = {u: caps.get(u, 0) / sum(caps.values()) for u in UNIT_TYPES}
    rate, icu_corr = 45.0, 1.0
    history = []
    for it in range(iterations):
        fit = {"arrivals_per_day": rate, "unit_weights": weights, "icu_transfer_correction": icu_corr}
        runs = [Simulation(SimParams(days=days, warmup_days=7, seed=seed + 100 * it + r, record_census=True),
                           cal, pool, fit).run()["kpis"] for r in range(reps)]
        occ = {u: float(np.mean([k["occupancy_by_unit"][u] for k in runs])) for u in runs[0]["occupancy_by_unit"]}
        total = float(np.mean([k["occupancy"] for k in runs]))
        history.append({"iteration": it, "rate": round(rate, 2), "occupancy": round(total, 4),
                        "by_unit": {u: round(v, 4) for u, v in occ.items()}, "icu_corr": round(icu_corr, 3)})
        if verbose:
            print(history[-1])
        rate *= (target / max(total, 0.05)) ** damping
        for u in weights:
            if u in occ and u != "ICU":
                weights[u] *= (target / max(occ[u], 0.05)) ** damping
        icu_corr *= (target / max(occ.get("ICU", target), 0.05)) ** damping
        icu_corr = float(min(max(icu_corr, 0.05), 1.5))
        z = sum(weights.values())
        weights = {u: w / z for u, w in weights.items()}
    return {"arrivals_per_day": round(rate, 3), "unit_weights": {u: round(w, 5) for u, w in weights.items()},
            "icu_transfer_correction": round(icu_corr, 4), "target_occupancy": target,
            "fit_days": days, "reps": reps, "history": history}
