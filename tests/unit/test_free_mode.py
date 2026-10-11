"""Free mode (item 18): a flow rule no spec section carries runs in a free_<stage> llm node, and code gates every
action that node brings back. The scenario: a weekly payday rule (M1 has one) on the Brief A fixture spec.
"""
import copy

import pytest

from builder import brain, compiler, contract, engine, toolapi
from builder import spec as sp
from tests.helpers import FIX, ROOT, load, mock_ledger
from tests.unit.test_dates import offers, promise, verified
from tests.unit.test_engine_slice import expect, play

PAYDAY = {"id": "X90", "stage": "negotiate", "class": "flow", "free_mode": True, "refs": [],
          "trigger": "the customer says on which weekday they are paid",
          "text": "If the customer says on which weekday they are paid, attempt 1 proposes the full amount on the first "
                  "business day after their next payday after today, within the deadline."}


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    s = load(FIX / "spec-a-v1.json")
    s["residual_rules"].append(copy.deepcopy(PAYDAY))
    return s


@pytest.fixture(scope="module")
def code(spec, api):
    return brain.source(spec, api)


PAID_ON_WEDNESDAYS = dict(stage="negotiate", main="refuses", event="free_x90",
                          said="I can't pay today, I get paid on Wednesdays.")


def free(**kw):
    return dict(stage="free_negotiate", said=kw.pop("said", "Yes."), **kw)


def routes(r):
    return [t["node"] for t in r.transcript if t["who"] == "agent"]


# ---------- acceptance ----------

def test_payday_weekday_records_the_gated_promise(spec, api, code):
    acts = verified("PUB-07") + [
        PAID_ON_WEDNESDAYS,
        free(free_action="promise", free_product="p1", free_amount="540", free_date="2026-10-22",
             said="Yes, Thursday the 22nd works."),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-07", acts)
    expect(r, "promise", promises=[promise("p1", 540.0, "2026-10-22")])
    assert "free_negotiate" in routes(r)
    assert "Thursday, 22 October" in r.agent_texts()[-2] and r.agent_texts()[-2].endswith("Do you confirm?")


def test_invalid_free_actions_are_rejected(spec, api, code):
    acts = verified("PUB-07") + [
        PAID_ON_WEDNESDAYS,
        free(free_action="promise", free_product="p9", free_amount="540", free_date="2026-10-22"),
        free(free_action="promise", free_product="p1", free_amount="540", free_date="2026-11-30"),
        free(free_action="promise", free_product="p1", free_amount="50", free_date="2026-10-22"),
        free(free_action="promise", free_product="p1", free_amount="540", free_date="2026-10-24"),
        free(free_action="promise", free_product="personal loan", free_amount="540", free_date="2026-10-22"),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-07", acts)
    expect(r, "promise", promises=[promise("p1", 540.0, "2026-10-22")])
    said = [t["text"] for t in r.transcript if t["who"] == "agent" and t["node"] == "free_negotiate"]
    assert "cannot take a promise" in said[1]
    assert "after the latest promise date, Monday, 2 November" in said[2]
    assert "below the lowest amount that can be recorded, 180" in said[3]
    assert "Saturday, 24 October is not a business day" in said[4]
    assert not any(c["role"] == "record_promise" and c["body"]["date"] != "2026-10-22" for c in r.stub.ledger["calls"])


# ---------- leaving free mode ----------

def test_done_goes_back_to_the_question_that_was_asked(spec, api, code):
    acts = verified("PUB-07") + [PAID_ON_WEDNESDAYS, free(free_action="done", said="Never mind."),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-07", acts)
    expect(r, "promise", promises=[promise("p1", 540.0, "2026-10-14")])
    assert r.agent_texts()[-3].endswith("Can you make this payment today?")


def test_free_mode_ends_after_its_turn_limit(spec, api, code):
    acts = verified("PUB-07") + [PAID_ON_WEDNESDAYS] + [free(free_action="continue")] * (engine.FREE_LIMIT + 1)
    r = play(spec, api, code, "PUB-07", acts)
    assert routes(r)[-1] == "talk_negotiate"


def test_a_free_transfer_goes_through_the_situation(spec, api, code):
    acts = verified("PUB-07") + [PAID_ON_WEDNESDAYS, free(free_action="transfer", free_target="restructuring")]
    expect(play(spec, api, code, "PUB-07", acts), "transferred", "restructuring", transfers=[{"queue": "restructuring"}])


def test_an_unknown_outcome_is_rejected(spec, api, code):
    acts = verified("PUB-07") + [PAID_ON_WEDNESDAYS, free(free_action="close", free_target="written_off")]
    r = play(spec, api, code, "PUB-07", acts)
    assert routes(r)[-1] == "free_negotiate" and "not an outcome the bank allows" in r.agent_texts()[-1]
    assert mock_ledger(r.stub)["outcome"] != "written_off"


# ---------- build side ----------

def test_the_free_rule_becomes_an_event_and_a_node(spec, api):
    fields = contract.extraction(spec)
    ev = next(f for f in fields["negotiate"] if f["name"] == "x_event")
    assert "free_x90" in ev["enum"] and PAYDAY["trigger"] in ev["description"]
    assert "free_x90" not in next(f for f in fields["identity"] if f["name"] == "x_event")["enum"]
    wf = compiler.compile_workflow(spec, api)
    node = wf["nodes"]["free_negotiate"]
    assert node["on_enter"]["type"] == "llm" and "{{free_rule}}" in node["task_message"]
    assert {f["name"] for f in node["extraction_schema"]} >= {"x_free_action", "x_free_product", "x_free_date"}
    assert "free_identity" not in wf["nodes"] and compiler.lint(wf) == []


def test_a_spec_without_free_rules_has_no_free_node(api):
    wf = compiler.compile_workflow(load(FIX / "spec-a-v1.json"), api)
    assert not any(n.startswith("free_") for n in wf["nodes"])


@pytest.mark.parametrize("change,msg", [({"class": "speech"}, "only a flow rule"), ({"stage": "any"}, "needs the one stage")])
def test_free_mode_needs_a_flow_rule_in_one_stage(spec, api, change, msg):
    s = copy.deepcopy(spec)
    s["residual_rules"][-1].update(change)
    assert any(msg in e for e in sp.validate(s, api).errors)


def test_a_bare_yes_asks_back_and_one_open_product_is_implied(spec, api, code):
    acts = verified("PUB-07") + [
        PAID_ON_WEDNESDAYS,
        free(free_action="promise", said="Yes, I confirm."),
        free(free_action="promise", free_amount="540", free_date="2026-10-22", said="540 on Thursday the 22nd."),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-07", acts)
    expect(r, "promise", promises=[promise("p1", 540.0, "2026-10-22")])
    said = [t["text"] for t in r.transcript if t["who"] == "agent" and t["node"] == "free_negotiate"]
    assert "say the amount and the date" in said[1]


def test_every_enum_slot_is_reset_to_a_member_of_its_enum(spec):
    for fields in contract.extraction(spec).values():
        for f in fields:
            slot = f["name"][len("x_"):]
            if f.get("enum"):
                assert slot in engine.ENUM_SLOTS and "none" in f["enum"], f["name"]
            else:
                assert slot not in engine.ENUM_SLOTS, f["name"]
