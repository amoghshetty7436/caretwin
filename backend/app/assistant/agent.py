"""CareTwin assistant: tool-calling over the hospital graph with verified citations.

Two interchangeable planners:

* ``OpenAICompatAgent`` - any OpenAI-compatible chat-completions endpoint with tool calling
  (Groq free tier, Google Gemini's OpenAI endpoint, local Ollama, OpenRouter, OpenAI).
* ``OfflinePlanner``     - deterministic intent parser + templated answers. Needs no API
  key, so CI, tests and the eval baseline are free and reproducible.

Both return the same shape and every citation is checked against the tool outputs that
were actually produced in this turn ("grounded" citations).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx
from neo4j import Driver

from app.assistant import tools as T
from app.config import Settings, get_settings

CITE_RE = re.compile(r"\[([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+|HOSP)\]")
ID_RE = re.compile(r"\b(?:AST|CKT|PNL|ATS|GEN|UPS|SW|CORE|ZV|O2|AIR|UTIL|BED|RM|WARD|STF|PT|FL|SHIFT|CERT)"
                   r"-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.I)

SYSTEM_PROMPT = """You are CareTwin, an operations assistant for a 300-bed hospital digital twin.
Answer ONLY from tool results; never invent counts, ids or names. Resolve names to ids with
search_entities before calling other tools. Cite every fact with the node id in square brackets,
e.g. "MICU has 3 free beds [WARD-MICU]". Ward codes: ED, PACU, OR, IMG, MICU, SICU, CVICU, CCU, SDU,
CARD, MEDA, NEURO, MEDB, ONC, SURG, ORTHO, TXP, PSY. Be concise: 1-4 sentences, numbers first."""


@dataclass
class AssistantResult:
    answer: str
    citations: list[str]
    tool_calls: list[dict] = field(default_factory=list)
    provider: str = "offline"
    grounded: bool = True
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _verify(answer: str, tool_calls: list[dict]) -> tuple[list[str], bool]:
    """Keep only cited ids that appear in this turn's tool outputs."""
    seen: set[str] = set()
    for c in tool_calls:
        seen.update(c.get("citations", []))
        seen.update(m.upper() for m in ID_RE.findall(json.dumps(c.get("result", ""), default=str)))
    cited = list(dict.fromkeys(CITE_RE.findall(answer)))
    valid = [c for c in cited if c in seen]
    return valid, bool(valid) and len(valid) == len(cited)


# ---------------------------------------------------------------------------
# LLM agent (OpenAI-compatible)
# ---------------------------------------------------------------------------
class OpenAICompatAgent:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.s = settings
        self.client = client or httpx.Client(timeout=settings.llm_timeout_s)

    def ask(self, driver: Driver, question: str) -> AssistantResult:
        t0 = time.perf_counter()
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
        calls: list[dict] = []
        answer = ""
        for _ in range(self.s.llm_max_tool_rounds):
            resp = self._post({"model": self.s.llm_model, "messages": messages, "temperature": 0,
                               "tools": T.openai_tool_specs(), "tool_choice": "auto"})
            if resp.status_code >= 400:  # surface the provider's reason (bad model id, rate limit, ...)
                raise httpx.HTTPStatusError(f"{resp.status_code} from LLM provider: {resp.text[:300]}",
                                            request=resp.request, response=resp)
            msg = resp.json()["choices"][0]["message"]
            tcs = msg.get("tool_calls") or []
            if not tcs:
                answer = msg.get("content") or ""
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = T.run_tool(driver, name, args)
                calls.append({"tool": name, "args": args, "citations": out["citations"], "result": out["data"]})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", name), "name": name,
                                 "content": json.dumps({"data": out["data"], "cite_ids": out["citations"]},
                                                       default=str)[:6000]})
        else:
            answer = answer or "I could not finish within the tool-call budget."
        cites, grounded = _verify(answer, calls)
        return AssistantResult(answer=answer, citations=cites, tool_calls=_slim(calls), provider=self.s.llm_model,
                               grounded=grounded, latency_ms=(time.perf_counter() - t0) * 1000)


    def _post(self, body: dict, retries: int = 6) -> httpx.Response:
        """POST with backoff on 429/5xx (free tiers enforce tight per-minute limits)."""
        resp = None
        for attempt in range(retries):
            resp = self.client.post(f"{self.s.llm_base_url.rstrip('/')}/chat/completions",
                                    headers={"Authorization": f"Bearer {self.s.llm_api_key}"}, json=body)
            if resp.status_code not in (429, 500, 502, 503):
                return resp
            try:
                wait = float(resp.headers.get("retry-after", 0))
            except ValueError:
                wait = 0.0
            time.sleep(min(60.0, max(wait, 2.0 * 2 ** attempt)))
        return resp


def _slim(calls: list[dict]) -> list[dict]:
    return [{"tool": c["tool"], "args": c["args"], "citations": c["citations"][:12]} for c in calls]


# ---------------------------------------------------------------------------
# Offline deterministic planner
# ---------------------------------------------------------------------------
WARD_ALIASES: list[tuple[str, str]] = [
    (r"\bmedical (icu|intensive care)\b|\bmicu\b", "MICU"), (r"\bsurgical (icu|intensive care)\b|\bsicu\b", "SICU"),
    (r"\bcardiovascular (icu|intensive care)\b|\bcvicu\b", "CVICU"), (r"\bcoronary care\b|\bccu\b", "CCU"),
    (r"\bstep-?down\b|\bsdu\b", "SDU"), (r"\bcardiology( ward)?\b|\bcard ward\b", "CARD"),
    (r"\bmedicine a\b|\bmed ?a\b|\bmeda\b", "MEDA"), (r"\bmedicine b\b|\bmed ?b\b|\bmedb\b", "MEDB"),
    (r"\bneurology\b|\bneuro ward\b|\bneuro\b", "NEURO"), (r"\b(hem(atology)?/?\s?onc(ology)?|oncology|onc)\b", "ONC"),
    (r"\bgeneral surgery\b|\bsurgery ward\b|\bsurg ward\b", "SURG"), (r"\borthop(a)?edics?\b|\bortho\b", "ORTHO"),
    (r"\btransplant\b|\btxp\b", "TXP"), (r"\bbehavio(u)?ral health\b|\bpsych(iatry|iatric)?\b|\bpsy\b", "PSY"),
    (r"\bemergency( department| room)?\b|\bthe ed\b|\bed\b|\ber\b", "ED"), (r"\bpacu\b|\bpost-anesthesia\b", "PACU"),
    (r"\boperating (suite|room|rooms|theatre)s?\b", "OR"), (r"\bimaging\b|\bradiology\b", "IMG"),
]
UNIT_TYPES = {"icu": "ICU", "intensive care": "ICU", "critical care": "ICU", "stepdown": "STEPDOWN",
              "cardiac": "CARD", "medicine": "MED", "medical-surgical": "MED", "med/surg": "MED",
              "neurology": "NEURO", "oncology": "ONC", "surgical": "SURG", "transplant": "TRANSPLANT",
              "psychiatric": "PSYCH", "psych": "PSYCH"}
ASSET_ALIASES: list[tuple[str, str]] = [
    (r"transport vent(ilator)?s?", "transport_ventilator"), (r"ventilators?|vents?\b", "ventilator"),
    (r"infusion pumps?|iv pumps?", "infusion_pump"), (r"pca pumps?", "pca_pump"), (r"feeding pumps?", "feeding_pump"),
    (r"scd pumps?|scds?\b|compression devices?", "scd_pump"),
    (r"central (monitoring )?stations?", "central_monitoring_station"),
    (r"telemetry( transmitters?| packs?| units?)?", "telemetry_transmitter"),
    (r"vitals? (monitors?|carts?)", "vitals_monitor"), (r"(patient |bedside )?monitors?", "patient_monitor"),
    (r"defibrillators?|defibs?\b", "defibrillator"), (r"dialysis( machines?)?|crrt machines?", "dialysis_machine"),
    (r"ct scanners?|\bct\b", "ct_scanner"), (r"mri( scanners?)?", "mri_scanner"),
    (r"portable x-?rays?", "portable_xray"), (r"x-?ray rooms?", "xray_room"), (r"fluoroscopy", "fluoroscopy_unit"),
    (r"wheelchairs?", "wheelchair"), (r"crash carts?", "crash_cart"), (r"glucometers?", "glucometer"),
    (r"bladder scanners?", "bladder_scanner"), (r"(portable )?ultrasounds?", "portable_ultrasound"),
    (r"suction( units?)?", "portable_suction"), (r"anesthesia machines?", "anesthesia_machine"),
    (r"stretchers?", "stretcher"), (r"(smart )?bed frames?", "smart_bed_frame"),
    (r"surgical lights?", "surgical_light"), (r"or tables?|operating tables?", "or_table"),
    (r"electrosurgical units?|esus?\b", "electrosurgical_unit"),
]
ROLE_ALIASES: list[tuple[str, str]] = [
    (r"charge nurses?", "charge_nurse"), (r"float(-| )pool nurses?", "float_pool_nurse"),
    (r"respiratory therapists?|\brts?\b", "respiratory_therapist"), (r"nurse anesthetists?|anesthetists?", "anesthetist"),
    (r"\b(registered )?nurses?\b|\brns?\b", "registered_nurse"), (r"residents?", "resident"),
    (r"physicians?|doctors?|attendings?", "physician"), (r"pharmacists?", "pharmacist"),
    (r"patient care techs?|pcts?\b|nursing assistants?", "patient_care_tech"), (r"surgical techs?", "surgical_tech"),
    (r"transporters?", "transporter"), (r"biomed(ical)? engineers?|biomed\b", "biomedical_engineer"),
    (r"case managers?", "case_manager"), (r"unit clerks?", "unit_clerk"), (r"bed managers?", "bed_manager"),
    (r"security( officers?)?", "security_officer"), (r"radiology techs?|rad techs?", "radiology_tech"),
    (r"environmental services|evs\b|housekeep\w*", "environmental_services"),
]
CERTS = ["ACLS", "BLS", "PALS", "CCRN", "TNCC", "CEN", "OCN", "CNOR", "VENT", "CRRT", "NIHSS", "CPI"]
FAIL_RE = re.compile(r"\b(fail|fails|failed|failure|goes down|go down|went down|outage|loses? power|lost|trips?|"
                     r"tripped|offline|breaks?|broke|shuts? down|out of service|impact|what happens|affected|"
                     r"knocked out|dies)\b")
DEPEND_RE = re.compile(r"\b(depend|depends|dependency|dependencies|powered by|power source|fed by|"
                       r"upstream|supplied by|backup power|rely|relies)\b")


def _match(aliases: list[tuple[str, str]], text: str) -> str | None:
    for pat, val in aliases:
        if re.search(pat, text):
            return val
    return None


class OfflinePlanner:
    provider = "offline"

    def ask(self, driver: Driver, question: str) -> AssistantResult:
        t0 = time.perf_counter()
        self.calls: list[dict] = []
        self.d = driver
        try:
            answer = self._answer(question)
        except Exception as exc:  # pragma: no cover - defensive
            answer = f"Sorry, I could not answer that ({exc.__class__.__name__})."
        cites, grounded = _verify(answer, self.calls)
        return AssistantResult(answer=answer, citations=cites, tool_calls=_slim(self.calls), provider="offline",
                               grounded=grounded, latency_ms=(time.perf_counter() - t0) * 1000)

    # -- helpers ----------------------------------------------------------------
    def _tool(self, name: str, **args) -> dict:
        out = T.run_tool(self.d, name, args)
        self.calls.append({"tool": name, "args": args, "citations": out["citations"], "result": out["data"]})
        return out["data"]

    def _ward(self, text: str) -> str | None:
        m = re.search(r"\bward-([a-z]+)\b", text)
        if m:
            return m.group(1).upper()
        return _match(WARD_ALIASES, text)

    def _resolve_infra(self, text: str) -> list[str]:
        ids = [m.upper() for m in ID_RE.findall(text) if not m.upper().startswith(("PT-", "WARD-", "STF-"))]
        if ids:
            return list(dict.fromkeys(ids))
        out: list[str] = []
        for m in re.finditer(r"generator\s*#?\s*(\d)", text):
            out.append(f"GEN-{m.group(1)}")
        if re.search(r"\b(both|all|two) generators\b", text):
            out += ["GEN-1", "GEN-2"]
        if re.search(r"utility( feed| power)?|city power|grid|mains", text):
            out.append("UTIL-1")
        if re.search(r"transfer switch", text):
            out.append("ATS-LS" if re.search(r"life.?safety", text) else "ATS-CR")
        for m in re.finditer(r"\bups\s*#?\s*(\d)", text):
            out.append(f"UPS-{m.group(1)}")
        for m in re.finditer(r"core (network )?switch\s*#?\s*(\d)", text):
            out.append(f"CORE-{m.group(2)}")
        for m in re.finditer(r"(?:floor|level)\s*(\d)\s*(?:network )?switch\s*([ab])\b", text):
            out.append(f"SW-{m.group(1)}{m.group(2).upper()}")
        for m in re.finditer(r"switch\s*(\d)([ab])\b", text):
            out.append(f"SW-{m.group(1)}{m.group(2).upper()}")
        for m in re.finditer(r"(?:floor|level)\s*(\d)\s*(normal|life.?safety|critical)( branch)? panel", text):
            b = {"normal": "N", "critical": "CR"}.get(m.group(2), "LS")
            out.append(f"PNL-{m.group(1)}{b}")
        for m in re.finditer(r"(normal|life.?safety|critical)( branch)? panel (?:on |for )?(?:floor|level)\s*(\d)", text):
            b = {"normal": "N", "critical": "CR"}.get(m.group(1), "LS")
            out.append(f"PNL-{m.group(3)}{b}")
        if re.search(r"bulk (liquid )?oxygen|oxygen tank|o2 tank", text):
            out.append("O2-BULK")
        elif re.search(r"(oxygen|o2) (reserve )?manifold|main oxygen|oxygen supply", text):
            out.append("O2-MAN")
        if re.search(r"air compressor|medical air plant", text):
            out.append("AIR-COMP")
        ward = self._ward(text)
        if ward and re.search(r"\b(oxygen|o2)\b", text) and "manifold" not in text:
            out.append(f"ZV-O2-{ward}")
        if ward and re.search(r"medical air", text):
            out.append(f"ZV-AIR-{ward}")
        m = re.search(r"circuit\s*#?\s*(\d+)", text)
        if ward and m:
            out.append(f"CKT-{ward}-{int(m.group(1)):02d}")
        m = re.search(r"#\s*(\d+)", text) or re.search(r"\bnumber (\d+)\b", text)
        kind = _match(ASSET_ALIASES, text)
        if kind and m and not out:
            out.append(f"AST-{kind.upper().replace('_', '-')}-{int(m.group(1)):04d}")
        if ward and re.search(r"central (monitoring )?station", text) and not out:
            res = self._tool("find_assets", kind="central_monitoring_station", ward=ward)
            out += [i["id"] for i in res["items"]]
        if not out:  # last resort: fulltext search
            cleaned = re.sub(r"what happens if|what if|the|fails?|goes down|is|are|an?|\?", " ", text)
            hits = self._tool("search_entities", query=cleaned.strip()[:80])
            hits = [h for h in hits if h["label"] in ("Infrastructure", "Asset")]
            if hits:
                out.append(hits[0]["id"])
        return list(dict.fromkeys(out))

    # -- intents ----------------------------------------------------------------
    def _answer(self, question: str) -> str:
        text = question.lower().strip()
        pm = re.search(r"\bpt-\d+-[a-z]?\d+\b", text)
        if pm:
            return self._patient(pm.group(0).upper())
        if DEPEND_RE.search(text):
            ids = self._resolve_infra(text) or [m.upper() for m in ID_RE.findall(text)]
            if ids:
                return self._upstream(ids[0])
        if FAIL_RE.search(text):
            ids = self._resolve_infra(text)
            if ids:
                return self._impact(ids)
        role = _match(ROLE_ALIASES, text)
        cert = next((c for c in CERTS if re.search(rf"\b{c.lower()}\b", text)), None)
        if cert is None and re.search(r"ventilat\w* (competen|certif)|vent[- ]certified", text):
            cert = "VENT"
        kind = _match(ASSET_ALIASES, text)
        if re.search(r"\bbeds?\b", text) and not re.search(r"bed frames?|bedside", text):
            kind = None
        if role or cert or re.search(r"\bstaff\b|\bwho\b.*\b(on duty|working)\b", text):
            if not (kind and not role):
                return self._staff(text, role, cert)
        if re.search(r"overdue|preventive maintenance|\bpm\b", text):
            return self._assets(text, kind, pm_overdue=True)
        if kind:
            return self._assets(text, kind)
        if re.search(r"inventory|equipment|devices|assets", text):
            return self._inventory()
        return self._capacity(text)

    def _patient(self, pid: str) -> str:
        res = self._tool("get_patient", patient_id=pid)
        if "error" in res:
            return f"Patient {pid} is not in the current census."
        p = res["patient"]
        assets = ", ".join(f"{a['kind'].replace('_', ' ')} [{a['id']}]" for a in res["bedside_assets"][:4])
        return (f"Patient {pid} [{pid}] is in bed {res['bed']} [{res['bed']}] on {res['ward_name']} "
                f"[WARD-{res['ward']}], acuity {p.get('acuity')}, age {p.get('age')}, reason: {p.get('reason')}."
                + (f" Bedside equipment: {assets}." if assets else ""))

    def _upstream(self, node_id: str) -> str:
        rows = self._tool("upstream_dependencies", node_id=node_id)
        if not rows:
            return f"{node_id} has no upstream dependencies in the graph [{node_id}]." if \
                self._tool("search_entities", query=node_id) else f"I could not find {node_id}."
        direct = [r for r in rows if r["hops"] == 1]
        chain = ", ".join(f"{r['name']} [{r['id']}]" for r in rows[:8])
        redundant = [r for r in rows if not r["required"]]
        extra = (f" Redundant (either-or) sources: {', '.join(f'[{r['id']}]' for r in redundant[:4])}."
                 if redundant else "")
        return (f"{node_id} [{node_id}] depends directly on {len(direct)} node(s) and on {len(rows)} upstream "
                f"node(s) in total: {chain}.{extra}")

    def _impact(self, ids: list[str]) -> str:
        res = self._tool("failure_impact", node_ids=ids)
        if "error" in res:
            return f"I could not find {', '.join(ids)} in the hospital graph."
        c = res["counts"]
        names = ", ".join(f"{f['name']} [{f['id']}]" for f in res["failed"])
        if not c["beds"] and not c["assets"] and len(res["infrastructure"]) <= len(ids):
            return (f"Failure of {names} has no downstream impact on its own: the loads it backs up have a redundant "
                    f"source, so no beds, equipment or patients are affected. Severity: low.")
        wards = ", ".join(f"{w['ward']} [WARD-{w['ward']}] ({w['patients']} patients)" for w in res["wards"][:5])
        s = (f"Failure of {names} is {res['severity'].upper()} severity: it affects {c['beds']} beds, "
             f"{c['assets']} assets and {c['patients']} current patients across {c['wards']} ward(s): {wards}.")
        hi = [p for p in res["patients"] if (p.get("acuity") or 0) >= 4]
        if hi:
            s += f" {len(hi)} high-acuity patients, e.g. {', '.join(f'[{p['id']}]' for p in hi[:3])}."
        if res["modes"].get("network"):
            s += f" {res['modes']['network']} devices lose network/central monitoring but keep working at the bedside."
        if res.get("min_battery_minutes"):
            s += (f" {c['assets_on_battery']} devices switch to battery; the shortest runway is "
                  f"{res['min_battery_minutes']} minutes.")
        if res["substitutes"]:
            sub = res["substitutes"][0]
            opts = ", ".join(f"[{o['id']}]" for o in sub["options"][:2])
            s += (f" Top replacement need: {sub['needed']} {sub['kind'].replace('_', ' ')}(s) for {sub['ward']}; "
                  f"{sub['available_elsewhere']} available unaffected" + (f", nearest {opts}." if opts else "."))
        return s

    def _staff(self, text: str, role: str | None, cert: str | None) -> str:
        ward = self._ward(text)
        shift = "NIGHT" if "night" in text else "EVE" if re.search(r"evening|\beve\b", text) else \
            "DAY" if re.search(r"day shift|\bdays\b", text) else None
        on_duty = True if re.search(r"on duty|on shift|right now|currently|working now", text) else None
        res = self._tool("find_staff", ward=ward, role=role, cert=cert, shift=shift, on_duty=on_duty)
        desc = " ".join(x for x in [
            "on-duty" if on_duty else None, (role or "staff").replace("_", " ") + ("s" if res["total"] != 1 else ""),
            f"with {cert}" if cert else None, f"on the {shift.lower()} shift" if shift else None,
            f"in {ward}" if ward else "hospital-wide"] if x)
        cite = f" [WARD-{ward}]" if ward else " [HOSP]"
        names = ", ".join(f"{s['name']} [{s['id']}]" for s in res["items"][:3])
        return f"There are {res['total']} {desc}{cite}." + (f" For example: {names}." if names else "")

    def _assets(self, text: str, kind: str | None, pm_overdue: bool | None = None) -> str:
        ward = self._ward(text) if not re.search(r"hospital|overall|in total|across", text) else None
        status = "available" if re.search(r"available|free|spare|unused|idle", text) else \
            "maintenance" if re.search(r"maintenance|repair|broken|out of service", text) and not pm_overdue else \
            "in_service" if re.search(r"in service|in use|being used", text) else None
        floor = None
        fm = re.search(r"(?:floor|level)\s*(\d)", text)
        if fm and not ward:
            floor = int(fm.group(1))
        res = self._tool("find_assets", kind=kind, ward=ward, status=status, floor=floor, pm_overdue=pm_overdue)
        label = (kind or "asset").replace("_", " ")
        where = f" in {ward} [WARD-{ward}]" if ward else (f" on floor {floor} [FL{floor}]" if floor is not None else
                                                          " hospital-wide [HOSP]")
        st = {"available": "available ", "maintenance": "in-maintenance ", "in_service": "in-service "}.get(status, "")
        ov = " with overdue preventive maintenance" if pm_overdue else ""
        ex = ", ".join(f"[{i['id']}]" for i in res["items"][:3])
        return (f"There are {res['total']} {st}{label}{'s' if res['total'] != 1 else ''}{ov}{where}."
                + (f" Examples: {ex}." if ex else ""))

    def _inventory(self) -> str:
        rows = self._tool("asset_kinds")
        summ = self._tool("hospital_summary")
        top = sorted(rows, key=lambda r: -r["total"])[:5]
        return (f"The hospital tracks {summ['by_label'].get('Asset', 0)} assets [HOSP]. Largest fleets: "
                + ", ".join(f"{r['kind'].replace('_', ' ')} {r['total']} ({r['available']} available)" for r in top)
                + ".")

    def _capacity(self, text: str) -> str:
        unit = None
        for k, v in UNIT_TYPES.items():
            if re.search(rf"\b{re.escape(k)}\b", text):
                unit = v
                break
        ward = self._ward(text)
        if ward and unit == "ICU" and ward in {"MICU", "SICU", "CVICU", "CCU"}:
            unit = None
        rank = re.search(r"\b(most|highest|fullest|busiest|least|lowest|emptiest)\b", text)
        if rank and not ward:
            wards = [w for w in self._tool("list_wards") if w["inpatient"]]
            if unit:
                wards = [w for w in wards if w["unit_type"] == unit]
            key = "available" if re.search(r"free|available|open|empty", text) else "occupancy"
            hi = rank.group(1) in {"most", "highest", "fullest", "busiest"}
            if key == "available":
                best = max(wards, key=lambda w: (w["available"], -w["floor"])) if hi or "emptiest" in text else \
                    min(wards, key=lambda w: (w["available"], w["floor"]))
            else:
                best = max(wards, key=lambda w: (w["occupancy"], w["code"])) if hi else \
                    min(wards, key=lambda w: (w["occupancy"], w["code"]))
            return (f"{best['name']} [{best['id']}] has the {rank.group(1)} {('free beds' if key == 'available' else 'occupancy')}: "
                    f"{best['occupied']}/{best['beds']} beds occupied ({best['occupancy']:.0%}), {best['available']} free.")
        if ward:
            w = self._tool("get_ward", code=ward)
            if "error" in w:
                return f"I don't know a ward called {ward}."
            occ = w["beds_occupied"] / w["beds_total"] if w["beds_total"] else 0
            free = ", ".join(w["free_beds"][:4])
            return (f"{w['ward']['name']} [WARD-{ward}] has {w['beds_total']} beds: {w['beds_occupied']} occupied and "
                    f"{w['beds_total'] - w['beds_occupied']} free ({occ:.0%} occupancy)."
                    + (f" Free beds include {free}." if free else ""))
        if unit:
            rows = self._tool("bed_availability", unit_type=unit)
            beds = sum(r["beds"] for r in rows)
            free = sum(r["available"] for r in rows)
            per = ", ".join(f"{r['ward']} {r['available']} [WARD-{r['ward']}]" for r in rows)
            return f"{unit} has {free} free of {beds} beds ({(beds - free) / beds:.0%} occupied): {per}."
        s = self._tool("hospital_summary")
        return (f"The hospital has {s['inpatient_beds']} inpatient beds with {s['occupied_beds']} occupied "
                f"({s['occupancy']:.0%}) and {s['inpatient_beds'] - s['occupied_beds']} free [HOSP]. The graph holds "
                f"{s['nodes']} nodes and {s['relationships']} relationships.")


def get_assistant(settings: Settings | None = None):
    s = settings or get_settings()
    if s.llm_provider == "openai_compat" and (s.llm_api_key or "localhost" in s.llm_base_url
                                              or "ollama" in s.llm_base_url):
        return OpenAICompatAgent(s)
    return OfflinePlanner()
