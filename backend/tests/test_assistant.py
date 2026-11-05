import json

import httpx
import pytest

from app.assistant import tools as T
from app.assistant.agent import OfflinePlanner, OpenAICompatAgent, _match, _verify, ASSET_ALIASES, ROLE_ALIASES, \
    WARD_ALIASES, get_assistant
from app.config import Settings
from tests.conftest import needs_neo4j


# ---------------------------------------------------------------- pure unit tests
@pytest.mark.parametrize("text,ward", [("the medical icu", "MICU"), ("sicu beds", "SICU"), ("stepdown", "SDU"),
                                       ("medicine b", "MEDB"), ("hem/onc", "ONC"), ("the er", "ED"),
                                       ("behavioral health", "PSY"), ("ortho", "ORTHO"), ("nothing", None)])
def test_ward_aliases(text, ward):
    assert _match(WARD_ALIASES, text) == ward


@pytest.mark.parametrize("text,kind", [("iv pumps", "infusion_pump"), ("spare vents", "ventilator"),
                                       ("transport ventilator", "transport_ventilator"), ("ct scanners", "ct_scanner"),
                                       ("telemetry units", "telemetry_transmitter"), ("defibs", "defibrillator")])
def test_asset_aliases(text, kind):
    assert _match(ASSET_ALIASES, text) == kind


@pytest.mark.parametrize("text,role", [("rns", "registered_nurse"), ("charge nurses", "charge_nurse"),
                                       ("rts", "respiratory_therapist"), ("doctors", "physician")])
def test_role_aliases(text, role):
    assert _match(ROLE_ALIASES, text) == role


def test_verify_drops_unseen_citations():
    calls = [{"citations": ["WARD-MICU"], "result": {"x": "BED-MICU-201A"}}]
    cites, grounded = _verify("ok [WARD-MICU] [BED-MICU-201A]", calls)
    assert cites == ["WARD-MICU", "BED-MICU-201A"] and grounded
    cites, grounded = _verify("made up [WARD-FAKE]", calls)
    assert cites == [] and not grounded
    assert _verify("no citations", calls) == ([], False)


def test_tool_specs_wellformed():
    specs = T.openai_tool_specs()
    assert len(specs) == len(T.TOOLS)
    for s in specs:
        assert s["type"] == "function" and s["function"]["parameters"]["type"] == "object"


def test_run_tool_unknown_and_bad_args():
    assert "error" in T.run_tool(None, "nope", {})["data"]


def test_get_assistant_selection():
    assert isinstance(get_assistant(Settings(llm_provider="offline")), OfflinePlanner)
    assert isinstance(get_assistant(Settings(llm_provider="openai_compat", llm_api_key="k")), OpenAICompatAgent)
    assert isinstance(get_assistant(Settings(llm_provider="openai_compat", llm_api_key="",
                                             llm_base_url="http://localhost:11434/v1")), OpenAICompatAgent)
    # no key and a hosted endpoint -> safe offline fallback
    assert isinstance(get_assistant(Settings(llm_provider="openai_compat", llm_api_key="")), OfflinePlanner)


# ---------------------------------------------------------------- integration
@needs_neo4j
@pytest.mark.parametrize("question,expect", [
    ("How many free beds does the Medical ICU have?", "WARD-MICU"),
    ("What if generator 2 fails?", "no downstream impact"),
    ("What happens if we lose utility power and generator 2?", "CRITICAL"),
    ("What does AST-VENTILATOR-0003 depend on?", "ZV-O2-MICU"),
    ("How many CCRN nurses work in the SICU?", "WARD-SICU"),
    ("How many infusion pumps are overdue for PM?", "overdue"),
    ("Which ward has the highest occupancy?", "highest"),
    ("What is the overall occupancy?", "[HOSP]"),
    ("How many ICU beds are free?", "ICU has"),
    ("Show me the equipment inventory", "assets [HOSP]"),
    ("What if the floor 2 critical panel trips?", "PNL-2CR"),
    ("What happens if UPS 3 fails?", "lose network"),
])
def test_offline_planner(seeded, question, expect):
    res = OfflinePlanner().ask(seeded["driver"], question)
    assert expect in res.answer, res.answer
    assert res.grounded and res.citations


@needs_neo4j
def test_offline_unknown_patient(seeded):
    res = OfflinePlanner().ask(seeded["driver"], "Where is patient PT-99-99999?")
    assert "not in the current census" in res.answer and not res.grounded


def _scripted_transport(script: list[dict]):
    calls = {"n": 0, "bodies": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["bodies"].append(json.loads(request.content))
        msg = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": msg}]})
    return httpx.MockTransport(handler), calls


@needs_neo4j
def test_openai_compat_agent_tool_loop(seeded):
    script = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_ward", "arguments": '{"code": "MICU"}'}}]},
        {"role": "assistant", "content": "MICU has free beds [WARD-MICU]."},
    ]
    transport, calls = _scripted_transport(script)
    agent = OpenAICompatAgent(Settings(llm_provider="openai_compat", llm_api_key="k", llm_base_url="https://x/v1"),
                              client=httpx.Client(transport=transport))
    res = agent.ask(seeded["driver"], "free beds in micu?")
    assert res.answer.startswith("MICU") and res.citations == ["WARD-MICU"] and res.grounded
    assert res.tool_calls[0]["tool"] == "get_ward"
    second = calls["bodies"][1]["messages"]
    assert second[-1]["role"] == "tool" and "MICU" in second[-1]["content"]
    assert calls["bodies"][0]["tools"]


@needs_neo4j
def test_openai_compat_agent_ungrounded_citation(seeded):
    transport, _ = _scripted_transport([{"role": "assistant", "content": "Trust me [WARD-FAKE]."}])
    agent = OpenAICompatAgent(Settings(llm_api_key="k"), client=httpx.Client(transport=transport))
    res = agent.ask(seeded["driver"], "hi there")
    assert res.citations == [] and not res.grounded


@needs_neo4j
def test_openai_compat_agent_round_limit(seeded):
    loop = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "hospital_summary", "arguments": "not json"}}]}
    transport, calls = _scripted_transport([loop])
    agent = OpenAICompatAgent(Settings(llm_api_key="k", llm_max_tool_rounds=2), client=httpx.Client(transport=transport))
    res = agent.ask(seeded["driver"], "loop forever")
    assert calls["n"] == 2 and "budget" in res.answer
