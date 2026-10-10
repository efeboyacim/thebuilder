"""The compiler (item 09): the hub graph, api bodies, extraction schemas, the lifecycle close and its hook, the lint."""
import copy, json

import pytest

from builder import compiler, engine, toolapi
from tests.helpers import FIX, ROOT, load


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    return load(FIX / "spec-a-v1.json")


@pytest.fixture(scope="module")
def wf(spec, api):
    return compiler.compile_workflow(spec, api)


def test_hub_graph(wf):
    n = wf["nodes"]
    assert wf["initial_node"] == "init" and compiler.lint(wf) == []
    assert [t["target_node"] for t in n["init"]["transitions"]] == ["opening"]
    assert [t["target_node"] for t in n["opening"]["transitions"]] == ["brain"]
    brain = [t["target_node"] for t in n["brain"]["transitions"]]
    assert brain[0] == "talk_error" and brain[-1] == "talk_fallback" and len(brain) == len(set(brain))
    assert n["brain"]["transitions"][0]["condition"]["conditions"][0] == {
        "variable": "brain_response.status", "operator": "equals", "operand": "error"}
    for stage in engine.STAGES:
        assert engine.talk_node(stage) in brain and n[engine.talk_node(stage)]["on_enter"] == {"type": "tts", "text": "{{say_next}}"}
    assert "api_record_note" not in n  # the fixture binds no note tool
    assert n["end_call"]["terminal"]["function"]["parameters"]["properties"]["spoken_message"]["default"] == "{{farewell}}"
    code = n["brain"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    assert code.startswith("def _brain(vars):") and "{{" not in code and "}}" not in code


def test_api_nodes_bake_the_brain_arguments(wf, spec):
    node = wf["nodes"]["api_record_promise"]
    tool = node["pre_actions"][0]
    body = tool["function"]["parameters"]["properties"]["body"]
    assert tool["function"]["parameters"]["properties"]["url"]["default"].endswith("/create_promise")
    assert body["properties"]["customer_id"]["default"] == "{{customer_id}}"
    assert body["properties"]["amount"] == {"type": "number", "default": "{{a_record_promise__amount}}",
                                            **{k: v for k, v in body["properties"]["amount"].items()
                                               if k not in ("type", "default")}}
    assert body["properties"]["product_id"]["default"] == "{{a_record_promise__product_id}}"
    assert set(body["required"]) >= {"customer_id", "product_id", "amount", "date"}
    assert tool["config"] == {"timeoutMs": 10000, "maxRetries": 0}
    assert {"path": "data.ok", "variable": "r_record_promise__ok"} in node["response_mapping"]
    assert node["passthrough"] and node["transitions"][-1]["condition"]["operator"] == "always"


def test_talk_nodes_extract_the_stage_contract(wf):
    ident = {f["name"]: f for f in wf["nodes"]["talk_identity"]["extraction_schema"]}
    assert set(ident) == {"x_main", "x_side_q", "x_said", "x_answer", "x_event", "x_reply"}
    assert ident["x_event"]["enum_values"] == ["human", "no_talk", "none"]
    assert ident["x_main"]["enum_values"][:3] == ["answers", "refuses", "unclear"] and all(
        f["min_confidence"] == compiler.MIN_CONFIDENCE for f in ident.values())
    opening = {f["name"]: f for f in wf["nodes"]["opening"]["extraction_schema"]}
    assert {"wrong_number", "deceased", "confirmed"} <= set(opening["x_main"]["enum_values"])
    fallback = {f["name"]: f for f in wf["nodes"]["talk_fallback"]["extraction_schema"]}
    assert {"x_answer", "x_day", "x_time"} <= set(fallback) and "yes" in fallback["x_main"]["enum_values"]
    assert not any(f["type"] in ("date", "datetime") for n in wf["nodes"].values() for f in n.get("extraction_schema") or [])


def _hook(tool, vars_, body):
    config = {"body": dict(body)}
    exec(tool["pre_process_code"], {}, {"config": config, "vars": vars_})
    return config["body"]


def test_lifecycle_close_and_its_hook(wf, spec):
    (close,) = wf["tools"]
    assert close["config"]["alwaysRunsAt"] == ["end_of_call"]
    body = close["function"]["parameters"]["properties"]["body"]["properties"]
    assert body["outcome"]["default"] == "{{final_outcome}}" and body["reason"]["default"] == "{{final_reason}}"
    sent = {"customer_id": "c1", "outcome": "model text", "reason": "UNKNOWN"}
    assert _hook(close, {"final_outcome": "promise", "final_reason": "-"}, sent) == {"customer_id": "c1", "outcome": "promise"}
    assert _hook(close, {"final_outcome": "not_verified", "final_reason": "failed"}, sent)["reason"] == "failed"
    sid = spec["closing"]["hangup"]["open"]["situation"]
    sit = next(s for s in spec["situations"] if s["id"] == sid)
    got = _hook(close, {}, sent)
    assert got["outcome"] == sit["outcome"] and got.get("reason") == (sit["reason"] or None)


def test_init_sets_the_opening_hang_up_pair(wf, spec):
    code = wf["nodes"]["init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    v = {}
    exec(code, {}, {"vars": v})
    sid = spec["closing"]["hangup"]["open"]["situation"]
    sit = next(s for s in spec["situations"] if s["id"] == sid)
    assert v["final_outcome"] == sit["outcome"] and v["final_reason"] == (sit["reason"] or "-")


def test_lint_catches_graph_errors(wf):
    bad = copy.deepcopy(wf)
    bad["nodes"]["brain"]["transitions"].insert(1, compiler.when("route", "x", "talk_open"))
    bad["nodes"]["api_verify"]["transitions"] = [compiler.when("route", "y", "brain")]
    bad["nodes"]["orphan"] = {"name": "orphan", "node_type": "conversation", "transitions": [compiler.always("nowhere")]}
    bad["nodes"]["talk_open"]["on_enter"]["text"] = None
    errs = compiler.lint(bad)
    assert "brain: two transitions to 'talk_open' (they merge)" in errs
    assert "api_verify: a passthrough node must end with an always transition" in errs
    assert "orphan: transition to unknown node 'nowhere'" in errs and "orphan: unreachable" in errs
    assert any(e.startswith("nodes.talk_open.on_enter.text: null") for e in errs)


def test_readback_diff_reports_stripped_fields(wf):
    got = copy.deepcopy(wf)
    got["nodes"]["talk_open"]["pre_actions"] = [{"x": 1}]
    got["nodes"]["api_lookup"]["pre_actions"] = []
    got["tools"][0].pop("pre_process_code")
    diffs = compiler.readback_diff(wf, got)
    assert "api_lookup.pre_actions: sent 1, stored 0" in diffs and "talk_open.pre_actions: sent 0, stored 1" in diffs
    assert any("pre_process_code missing" in d for d in diffs)
    assert compiler.readback_diff(wf, json.loads(json.dumps(wf))) == []
