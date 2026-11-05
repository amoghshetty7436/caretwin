"""Graph tools exposed to the assistant. Each returns ``{"data": ..., "citations": [node ids]}``.

Tool schemas use the OpenAI function-calling format, which Groq, Gemini (OpenAI-compatible
endpoint), Ollama, OpenRouter and OpenAI all accept.
"""
from __future__ import annotations

from typing import Any, Callable

from neo4j import Driver

from app.graph import queries as q

MAX_CITATIONS = 40


def _ids(items: list[dict], key: str = "id") -> list[str]:
    return [i[key] for i in items if isinstance(i, dict) and i.get(key)]


def t_search_entities(d: Driver, query: str, label: str | None = None) -> dict:
    rows = q.search(d, query, label=label, limit=8)
    return {"data": rows, "citations": _ids(rows)}


def t_hospital_summary(d: Driver) -> dict:
    return {"data": q.summary(d), "citations": ["HOSP"]}


def t_list_wards(d: Driver) -> dict:
    rows = q.list_wards(d)
    return {"data": rows, "citations": _ids(rows)}


def t_get_ward(d: Driver, code: str) -> dict:
    res = q.ward_detail(d, code)
    if res is None:
        return {"data": {"error": f"no ward with code {code}"}, "citations": []}
    beds = res["beds"]
    data = {"ward": res["ward"], "beds_total": len(beds), "beds_occupied": sum(1 for b in beds if b["patient_id"]),
            "free_beds": [b["id"] for b in beds if not b["patient_id"]],
            "patients": [b for b in beds if b["patient_id"]], "assets": res["assets"], "staff": res["staff"]}
    return {"data": data, "citations": [res["ward"]["id"]]}


def t_bed_availability(d: Driver, unit_type: str | None = None) -> dict:
    rows = q.bed_availability(d, unit_type)
    return {"data": rows, "citations": [f"WARD-{r['ward']}" for r in rows]}


def t_failure_impact(d: Driver, node_ids: list[str]) -> dict:
    res = q.failure_impact(d, node_ids)
    if "error" in res:
        return {"data": res, "citations": []}
    # keep the payload small for the LLM context window
    slim = {k: res[k] for k in ("failed", "category", "severity", "counts", "modes", "wards", "min_battery_minutes")}
    slim["patients"] = res["patients"][:15]
    slim["substitutes"] = res["substitutes"][:8]
    slim["infrastructure"] = [i["id"] for i in res["infrastructure"]][:20]
    cites = [f["id"] for f in res["failed"]] + [f"WARD-{w['ward']}" for w in res["wards"]] + \
        [p["id"] for p in res["patients"][:10]]
    return {"data": slim, "citations": cites}


def t_find_assets(d: Driver, kind: str | None = None, ward: str | None = None, status: str | None = None,
                  floor: int | None = None, pm_overdue: bool | None = None) -> dict:
    res = q.find_assets(d, kind=kind, ward=ward, status=status, floor=floor, pm_overdue=pm_overdue, limit=25)
    scope = ([f"WARD-{ward.upper()}"] if ward else []) + ([f"FL{floor}"] if floor is not None else []) or ["HOSP"]
    return {"data": res, "citations": scope + _ids(res["items"])}


def t_asset_kinds(d: Driver) -> dict:
    return {"data": q.asset_kinds(d), "citations": []}


def t_find_staff(d: Driver, ward: str | None = None, role: str | None = None, cert: str | None = None,
                 shift: str | None = None, on_duty: bool | None = None) -> dict:
    res = q.find_staff(d, ward=ward, role=role, cert=cert, shift=shift, on_duty=on_duty, limit=25)
    scope = [f"WARD-{ward.upper()}"] if ward else ["HOSP"]
    return {"data": res, "citations": scope + _ids(res["items"])}


def t_get_patient(d: Driver, patient_id: str) -> dict:
    res = q.get_patient(d, patient_id)
    if not res:
        return {"data": {"error": f"no patient {patient_id} in the current census"}, "citations": []}
    return {"data": res, "citations": [patient_id, res["bed"], f"WARD-{res['ward']}"]}


def t_upstream(d: Driver, node_id: str) -> dict:
    rows = q.upstream_dependencies(d, node_id)
    return {"data": rows, "citations": [node_id] + _ids(rows)}


_S = {"type": "string"}
TOOLS: dict[str, tuple[Callable[..., dict], dict]] = {
    "search_entities": (t_search_entities, {
        "description": "Fuzzy-search hospital graph nodes (wards, beds, assets, infrastructure, staff, patients) "
                       "by name or id. Use this to resolve a name like 'generator 2' to a node id.",
        "parameters": {"type": "object", "properties": {"query": _S, "label": {
            "type": "string", "enum": ["Ward", "Bed", "Asset", "Infrastructure", "Staff", "Patient", "Room"]}},
            "required": ["query"]}}),
    "hospital_summary": (t_hospital_summary, {
        "description": "Node counts and current hospital-wide inpatient occupancy.",
        "parameters": {"type": "object", "properties": {}}}),
    "list_wards": (t_list_wards, {
        "description": "All wards with code, unit type, floor, beds, occupied, available, occupancy and on-duty staff.",
        "parameters": {"type": "object", "properties": {}}}),
    "get_ward": (t_get_ward, {
        "description": "Detail for one ward by code (e.g. MICU, MEDA, ED): beds, patients, assets by status, staff.",
        "parameters": {"type": "object", "properties": {"code": _S}, "required": ["code"]}}),
    "bed_availability": (t_bed_availability, {
        "description": "Free inpatient beds per ward, optionally filtered to a unit type "
                       "(ICU, STEPDOWN, CARD, MED, NEURO, ONC, SURG, TRANSPLANT, PSYCH).",
        "parameters": {"type": "object", "properties": {"unit_type": _S}}}),
    "failure_impact": (t_failure_impact, {
        "description": "Simulate failure of one or more assets/infrastructure nodes by id. Returns affected wards, "
                       "beds, patients (by acuity), battery runway and available substitute equipment.",
        "parameters": {"type": "object", "properties": {"node_ids": {"type": "array", "items": _S}},
                       "required": ["node_ids"]}}),
    "find_assets": (t_find_assets, {
        "description": "Count and list equipment. kind examples: ventilator, infusion_pump, patient_monitor, "
                       "defibrillator, dialysis_machine, ct_scanner. status: in_service | available | maintenance.",
        "parameters": {"type": "object", "properties": {"kind": _S, "ward": _S, "status": {
            "type": "string", "enum": ["in_service", "available", "maintenance"]}, "floor": {"type": "integer"},
            "pm_overdue": {"type": "boolean"}}}}),
    "asset_kinds": (t_asset_kinds, {
        "description": "Inventory of every asset kind with total / available / maintenance counts.",
        "parameters": {"type": "object", "properties": {}}}),
    "find_staff": (t_find_staff, {
        "description": "Count and list staff. role examples: registered_nurse, physician, respiratory_therapist, "
                       "charge_nurse. cert examples: ACLS, CCRN, VENT, CRRT, TNCC. shift: DAY | EVE | NIGHT.",
        "parameters": {"type": "object", "properties": {"ward": _S, "role": _S, "cert": _S, "shift": _S,
                                                        "on_duty": {"type": "boolean"}}}}),
    "get_patient": (t_get_patient, {
        "description": "Locate a patient in the current census by id (e.g. PT-42-00123): bed, ward, bedside assets.",
        "parameters": {"type": "object", "properties": {"patient_id": _S}, "required": ["patient_id"]}}),
    "upstream_dependencies": (t_upstream, {
        "description": "What a node depends on (power circuit, panel, transfer switch, generator, gas, network).",
        "parameters": {"type": "object", "properties": {"node_id": _S}, "required": ["node_id"]}}),
}


def openai_tool_specs() -> list[dict]:
    return [{"type": "function", "function": {"name": name, **spec}} for name, (_, spec) in TOOLS.items()]


def run_tool(driver: Driver, name: str, args: dict[str, Any]) -> dict:
    if name not in TOOLS:
        return {"data": {"error": f"unknown tool {name}"}, "citations": []}
    fn, spec = TOOLS[name]
    allowed = set(spec["parameters"].get("properties", {}))
    clean = {k: v for k, v in (args or {}).items() if k in allowed and v not in (None, "")}
    try:
        out = fn(driver, **clean)
    except TypeError as exc:
        return {"data": {"error": f"bad arguments for {name}: {exc}"}, "citations": []}
    out["citations"] = list(dict.fromkeys(out["citations"]))[:MAX_CITATIONS]
    return out
