"""Amounts, events and side questions (item 12) on the Brief A fixture spec, through the brain code string and the
stub bank: floor offers (too much, below the floor), amounts the customer proposes, hardship with its precedence and
suppression, paid, dispute, restructuring, a human (asked once first), not wanting to talk (twice, before and after
verification), robot and scam answers, the llm twin for other questions with its facts guard, and loop guards.
"""
import copy

import pytest

from builder import brain, engine, toolapi
from tests.helpers import FIX, ROOT, load, mock_ledger
from tests.unit.test_dates import offers, promise, verified
from tests.unit.test_engine_slice import CASES, expect, play, score


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    return load(FIX / "spec-a-v1.json")


@pytest.fixture(scope="module")
def code(spec, api):
    return brain.source(spec, api)


def passes(r, case_id):
    res = score.score_ledger(CASES[case_id], mock_ledger(r.stub))
    assert res["pass"], (res["failed"], r.agent_texts())
    assert r.stub.ledger["violations"] == []


def routes(r):
    return [t["node"] for t in r.transcript if t["who"] == "agent"]


# ---------- acceptance scripts ----------

def test_pub04_robot_question_then_the_deadline(spec, api, code):
    acts = verified("PUB-04") + [
        dict(stage="negotiate", main="proposes", day="6 November", said="My salary arrives on 6 November."),
        dict(stage="negotiate", side_q="robot", said="Wait, am I talking to a robot?"),
        dict(stage="negotiate", main="accepts", said="OK, the 23rd then."),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-04", acts)
    passes(r, "PUB-04")
    said = offers(r)
    assert said[2].startswith("I am Freya Bank's digital assistant. The latest date I can record is Friday, 23 October.")


def test_pub05_hardship_declines_support(spec, api, code):
    acts = verified("PUB-05") + [
        dict(stage="negotiate", main="refuses", event="hardship", said="I lost my job last month, I can't pay anything."),
        dict(stage="negotiate", side_q="other", event="restructuring", said="Can I get a discount?"),
        dict(stage="negotiate", reply="no", main="refuses", said="No, I don't want to be transferred.")]
    r = play(spec, api, code, "PUB-05", acts)
    passes(r, "PUB-05")
    said = offers(r)
    assert said[1] == "I am sorry to hear that. I can connect you with our customer support team. Would you like that?"
    assert routes(r)[-2] == "talk_negotiate_llm"  # the discount question goes to the llm twin, the offer is repeated
    assert r.vars.get("side_question") == "Can I get a discount?"
    assert not any("Could you pay" in t for t in said)


def test_pub06_too_much_then_the_floor_on_friday(spec, api, code):
    acts = verified("PUB-06") + [
        dict(stage="negotiate", main="too_much", said="That's too much right now."),
        dict(stage="negotiate", main="refuses", said="Not today, no."),
        dict(stage="negotiate", main="accepts", said="Friday works."),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-06", acts)
    passes(r, "PUB-06")
    said = offers(r)
    assert said[1] == ("I understand. The lowest amount I can record is 637.50, and the rest stays overdue. "
                       "Can you pay 637.50 on Wednesday, 14 October?")
    assert said[2] == "Could you make that payment on Friday, 16 October instead?"
    assert sum("637.50" in t for t in said[:3]) == 1


def test_pub06_customer_names_friday_after_the_floor(spec, api, code):
    acts = verified("PUB-06") + [dict(stage="negotiate", main="too_much"),
                                 dict(stage="negotiate", main="proposes", day="this Friday"),
                                 dict(stage="confirm", main="yes")]
    passes(play(spec, api, code, "PUB-06", acts), "PUB-06")


def test_pub08_already_paid(spec, api, code):
    acts = verified("PUB-08") + [dict(stage="negotiate", main="refuses", event="paid",
                                      said="I paid it in the app this morning.")]
    r = play(spec, api, code, "PUB-08", acts)
    passes(r, "PUB-08")
    assert "Payments can take one business day to appear." in r.agent_texts()[-1]


def test_pub09_restructuring_beats_too_much(spec, api, code):
    acts = verified("PUB-09") + [dict(stage="negotiate", main="too_much", event="restructuring",
                                      said="I can't pay this amount, I want lower instalments.")]
    r = play(spec, api, code, "PUB-09", acts)
    passes(r, "PUB-09")
    assert [c["role"] for c in r.stub.ledger["calls"]][-2:] == ["transfer", "close"]


# ---------- amounts ----------

@pytest.mark.parametrize("said,want", [("I can pay 300 on Friday", 300.0), ("the full amount on Friday", 780.0),
                                       ("two hundred and sixty on Friday", 260.0), ("900 on Friday", 780.0)])
def test_an_amount_from_the_floor_up_is_accepted(spec, api, code, said, want):
    acts = verified("PUB-05") + [dict(stage="negotiate", main="proposes", amount=said, day="Friday"),
                                 dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-05", acts), "promise", promises=[promise("p1", want, "2026-10-16")])


def test_below_the_floor_offers_the_floor_once(spec, api, code):
    acts = verified("PUB-05") + [dict(stage="negotiate", main="proposes", amount="100", said="I can do 100."),
                                 dict(stage="negotiate", main="proposes", amount="150", said="What about 150?"),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-05", acts)
    expect(r, "promise", promises=[promise("p1", 260.0, "2026-10-16")])
    said = offers(r)
    assert said[1].startswith("I cannot record 100. The lowest amount I can record is 260")
    assert said[2] == "Could you make that payment on Friday, 16 October instead?"  # the second low offer: attempt 1


def test_an_amount_change_at_the_read_back(spec, api, code):
    acts = verified("PUB-05") + [dict(stage="negotiate", main="accepts"),
                                 dict(stage="confirm", main="change", amount="300", said="Make it 300."),
                                 dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-05", acts), "promise", promises=[promise("p1", 300.0, "2026-10-14")])


def test_no_template_asks_how_much(spec, api, code):
    acts = verified("PUB-06") + [dict(stage="negotiate", main="too_much")] + \
        [dict(stage="negotiate", main="refuses")] * 3
    r = play(spec, api, code, "PUB-06", acts)
    expect(r, "no_promise", "refused")
    assert not any("how much" in t.lower() for t in r.agent_texts())


# ---------- events ----------

def test_hardship_accepts_support(spec, api, code):
    acts = verified("PUB-05") + [dict(stage="negotiate", event="hardship", said="I've been very ill."),
                                 dict(stage="negotiate", reply="yes", said="Yes please.")]
    expect(play(spec, api, code, "PUB-05", acts), "transferred", "support", transfers=[{"queue": "support"}])


def test_hardship_then_own_date_is_recorded(spec, api, code):
    acts = verified("PUB-05") + [dict(stage="negotiate", event="hardship", said="I lost my job."),
                                 dict(stage="negotiate", main="proposes", day="Friday", said="But I can pay Friday."),
                                 dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-05", acts), "promise", promises=[promise("p1", 780.0, "2026-10-16")])


def test_hardship_hang_up_closes_as_hardship(spec, api, code):
    acts = verified("PUB-05") + [dict(stage="negotiate", event="hardship")]
    expect(play(spec, api, code, "PUB-05", acts), "no_promise", "hardship")


def test_hardship_suppresses_restructuring_and_persuasion(spec, api, code):
    acts = verified("PUB-09") + [dict(stage="negotiate", event="hardship"),
                                 dict(stage="negotiate", event="restructuring", main="refuses",
                                      said="Can't I just have lower instalments?")]
    r = play(spec, api, code, "PUB-09", acts)
    expect(r, "no_promise", "hardship")
    assert not any(c["role"] == "transfer" for c in r.stub.ledger["calls"])


def test_dispute(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", event="dispute", said="I closed that card years ago.")]
    expect(play(spec, api, code, "PUB-01", acts), "no_promise", "dispute")


def test_human_is_asked_once_then_transferred(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", event="human", said="I want a real person."),
                                 dict(stage="negotiate", reply="no", said="No, put me through to a person.")]
    r = play(spec, api, code, "PUB-01", acts)
    expect(r, "transferred", "human", transfers=[{"queue": "general"}])
    assert offers(r)[1] == "Can I help you with anything first?"


def test_human_before_verification(spec, api, code):
    acts = [dict(stage="open", event="human", said="Is there a human I can talk to?"),
            dict(stage="open", event="human", said="I want a person.")]
    expect(play(spec, api, code, "PUB-01", acts), "transferred", "human", transfers=[{"queue": "general"}])


def test_human_then_continues(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", event="human"),
                                 dict(stage="negotiate", reply="yes", said="Fine, go on."),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-01", acts)
    expect(r, "promise", promises=[promise("p1", 420.0, "2026-10-14")])
    assert offers(r)[2].endswith("Can you make this payment today?")


def test_no_talk_twice_after_verification(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", event="no_talk", main="refuses"),
                                 dict(stage="negotiate", event="no_talk", main="refuses")]
    r = play(spec, api, code, "PUB-10", acts)
    expect(r, "no_promise", "refused")
    assert len(offers(r)) == 3  # the first one used attempt 1, the second closed


def test_no_talk_twice_before_verification(spec, api, code):
    acts = [dict(stage="open", main="confirmed"), dict(stage="identity", event="no_talk", main="refuses"),
            dict(stage="identity", event="no_talk", main="unclear", said="I said I don't want to talk.")]
    expect(play(spec, api, code, "PUB-01", acts), "not_verified", "declined")


def test_paid_hang_up_before_it_is_said_closes_by_stage(spec, api, code):
    expect(play(spec, api, code, "PUB-08", verified("PUB-08")), "no_promise", "refused")


# ---------- side questions ----------

def test_scam_before_confirmation_counts_as_the_question(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", side_q="scam", said="Is this a scam?"),
                                         dict(stage="open", main="confirmed"),
                                         dict(stage="identity", main="refuses"), dict(stage="identity", main="refuses")])
    expect(r, "not_verified", "declined")
    assert r.agent_texts()[1] == ("You can verify this call by phoning Freya Bank's customer service number. "
                                  "Am I speaking with Mr Reyes?")


def test_other_question_before_verification_gets_no_facts(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", side_q="other", said="How did you get this number?")])
    assert routes(r)[1] == "talk_open_llm" and r.vars["facts"] == ""


def test_other_question_after_verification_gets_the_facts(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", side_q="other", said="How late am I exactly?")]
    r = play(spec, api, code, "PUB-01", acts)
    assert routes(r)[-1] == "talk_negotiate_llm"
    assert "6 days" in r.vars["facts"] and "Offer on the table: 420 on Wednesday, 14 October." in r.vars["facts"]


def test_no_info_policy_says_the_sentence(spec, api):
    s = copy.deepcopy(spec)
    s["side_questions"]["other"]["policy"] = "no_info"
    acts = verified("PUB-01") + [dict(stage="negotiate", side_q="other", said="What's the interest rate?")]
    r = play(s, api, brain.source(s, api), "PUB-01", acts)
    assert routes(r)[-1] == "talk_negotiate"
    assert offers(r)[1].startswith("I am sorry, I do not have that information. The full past-due amount is 420.")


def test_a_question_at_the_read_back_is_not_counted(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", main="accepts"),
                                 dict(stage="confirm", side_q="robot"), dict(stage="confirm", side_q="scam"),
                                 dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-01", acts), "promise", promises=[promise("p1", 420.0, "2026-10-14")])


# ---------- guards ----------

def test_turn_limit_ends_an_endless_call(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", side_q="robot")] * (engine.TURN_LIMIT + 5)
    r = play(spec, api, code, "PUB-01", acts)
    assert r.ended == "end" and len(r.agent_texts()) <= engine.TURN_LIMIT + 2


def test_compiled_llm_twins(spec, api):
    from builder import compiler
    wf = compiler.compile_workflow(spec, api)
    twin = wf["nodes"]["talk_negotiate_llm"]
    assert twin["on_enter"]["type"] == "llm" and "{{facts}}" in twin["task_message"]
    assert "I am sorry, I do not have that information." in twin["task_message"]
    assert "talk_third_llm" not in wf["nodes"] and compiler.lint(wf) == []
