"""The slice engine through the brain code string and the stub bank (item 08).

Acceptance: the PUB-01, PUB-02 and PUB-03 act-scripts reach their expected ledgers (scored with the kit's own
tools/score.py). The other scenarios pin down the slice's flow rules on the Brief A v0 fixture spec: pieces and
words, the confirmation and decline counters, callback windows on other days, terminations, hints and hang-ups.
"""
import copy, json

import pytest

from builder import bindings as bd
from builder import brain, engine, replay, toolapi
from builder.stubbank import StubBank
from tests.helpers import FIX, ROOT, kit_score, load, mock_ledger

CASES = {c["id"]: c for c in load(ROOT / "public-test-set.json")}
score = kit_score()


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    return load(FIX / "spec-a-v0.json")


@pytest.fixture(scope="module")
def code(spec, api):
    return brain.source(spec, api)


def customer(case, hints=()):
    return {"identity": {"answers": dict(case["identity"]), "limit": 3, "hints": list(hints),
                         "readable": {"date_of_birth": r"^\d{4}-\d{2}-\d{2}$"}},
            "products": [{"ref": f"p{i + 1}", "kind": p["type"], "due_amount": p["past_due"],
                          "days_overdue": p["days_late"], "fields": dict(p)} for i, p in enumerate(case["products"])]}


def play(spec, api, code, case_id, acts, now=None, hints=(), faults=None):
    case = CASES[case_id]
    stub = StubBank(api, bd.Bindings.from_dict(spec["bindings"]), customer(case, hints), faults=faults)
    variables = dict(case["variables"], customer_id="c_" + case_id.lower())
    if now:
        variables["now"] = now
    return replay.Replay(spec, api, stub, variables, code=code).run(acts)


def expect(r, outcome, reason=None, promises=(), callbacks=(), transfers=()):
    led = mock_ledger(r.stub)
    case = {"expected": {"outcome": outcome, "reason": reason, "promises": list(promises), "notes": [],
                         "callbacks": list(callbacks), "transfers": list(transfers)}}
    res = score.score_ledger(case, led)
    assert res["pass"], (res["failed"], r.agent_texts())
    assert r.stub.ledger["violations"] == []


def said_before_lookup(r):
    out = []
    for t in r.transcript:
        if t["who"] == "tool" and t["tool"] == r.b.tools["lookup"]:
            break
        if t["who"] == "agent":
            out.append(t["text"])
    return out


def assert_nothing_disclosed(r, spec):
    for text in said_before_lookup(r):
        for word in spec["opening"]["forbidden_before_verification"]:
            assert word not in text.lower(), (word, text)


ACTS = {
    "PUB-01": [dict(stage="open", main="confirmed", said="Yes, speaking."),
               dict(stage="identity", main="answers", answer="14 March 1988", said="It's the 14th of March 1988."),
               dict(stage="identity", main="answers", answer="4471", said="4471."),
               dict(stage="negotiate", main="accepts", said="Yes, I can pay the full amount today."),
               dict(stage="confirm", main="yes", said="Yes.")],
    "PUB-02": [dict(stage="open", main="someone_else", side_q="purpose", said="This is her husband. What is this about?"),
               dict(stage="third", main="offers_to_pay", said="I can pay it for her, just tell me how much."),
               dict(stage="third", main="gives_time", day="tomorrow", time="around eleven",
                    said="She'll be here tomorrow morning, around eleven.")],
    "PUB-03": [dict(stage="open", main="confirmed", said="Yes, that's me."),
               dict(stage="identity", main="answers", answer="12 July 1991"),
               dict(stage="identity", main="answers", answer="21 August 1992"),
               dict(stage="identity", main="answers", answer="21 July 1990", said="21 July 1990. How much do I owe?")],
}


# ---------- acceptance: PUB-01, PUB-02, PUB-03 ----------

@pytest.mark.parametrize("case_id", sorted(ACTS))
def test_pub_act_scripts_reach_their_expected_ledgers(spec, api, code, case_id):
    r = play(spec, api, code, case_id, ACTS[case_id])
    res = score.score_ledger(CASES[case_id], mock_ledger(r.stub))
    assert res["pass"], (res["failed"], r.agent_texts())
    assert r.stub.ledger["violations"] == [] and r.ended == "end"
    assert_nothing_disclosed(r, spec)
    assert all("{{" not in t and "}}" not in t for t in r.agent_texts())


def test_pub01_says_what_the_brief_prescribes(spec, api, code):
    r = play(spec, api, code, "PUB-01", ACTS["PUB-01"])
    texts = r.agent_texts()
    assert "Am I speaking with Mr Reyes?" in texts[0]
    disclosure = next(t for t in texts if "collections" in t)
    assert "credit card ending 4821" in disclosure and "420" in disclosure and "6 days" in disclosure
    assert disclosure.endswith("Can you make this payment today?")
    assert "Do you confirm?" in texts[-2] and "Wednesday, 14 October" in texts[-2]
    assert "You will receive a text message confirming this promise." in texts[-1]
    assert texts[-1].endswith("Goodbye.")


def test_pub02_reveals_nothing_to_the_third_party(spec, api, code):
    r = play(spec, api, code, "PUB-02", ACTS["PUB-02"])
    assert not any(c["role"] in ("verify", "lookup") for c in r.stub.ledger["calls"])
    assert "I can only speak with Ms Costa" in r.agent_texts()[1]


def test_pub03_ends_the_check_after_three_wrong_answers(spec, api, code):
    r = play(spec, api, code, "PUB-03", ACTS["PUB-03"])
    assert [c["role"] for c in r.stub.ledger["calls"]] == ["verify", "verify", "verify", "close"]
    assert "mobile app" in r.agent_texts()[-1]


# ---------- how customers talk ----------

def test_answers_in_pieces_and_in_words(spec, api, code):
    acts = [dict(stage="open", main="confirmed", said="Mr Reyes, yes."),
            dict(stage="identity", main="answers", answer="the fourteenth of March", said="The fourteenth of March"),
            dict(stage="identity", main="answers", answer="nineteen eighty-eight"),
            dict(stage="identity", main="answers", answer="forty-four"),
            dict(stage="identity", main="answers", answer="seventy-one"),
            dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-01", acts)
    expect(r, "promise", promises=[{"product_id": "p1", "amount": 420.0, "date": "2026-10-14"}])
    sent = [c["body"]["answer"] for c in r.stub.ledger["calls"] if c["role"] == "verify"]
    assert sent == ["1988-03-14", "4471"]


def test_numeric_date_is_read_day_first_and_digits_in_words(spec, api, code):
    acts = [dict(stage="open", main="confirmed"),
            dict(stage="identity", main="answers", answer="14/03/1988"),
            dict(stage="identity", main="answers", answer="four four seven one"),
            dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-01", acts), "promise",
           promises=[{"product_id": "p1", "amount": 420.0, "date": "2026-10-14"}])


def test_regex_fallback_on_what_was_said(spec, api, code):
    acts = [dict(stage="open", said="Speaking, go on."), dict(stage="identity", main="refuses"),
            dict(stage="identity", main="refuses")]
    r = play(spec, api, code, "PUB-01", acts)
    expect(r, "not_verified", "declined")


# ---------- opening ----------

def test_unclear_twice_closes_as_unclear(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", main="unclear", said="Hello?"),
                                         dict(stage="open", main="unclear", said="Who is this?")])
    expect(r, "not_reached", "unclear")
    assert r.agent_texts()[1] == "Am I speaking with Mr Reyes?"


def test_a_question_then_the_reask_is_the_second_ask(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", side_q="robot", said="Are you a robot?"),
                                         dict(stage="open", main="unclear", said="Hmm.")])
    expect(r, "not_reached", "unclear")


def test_an_empty_extraction_is_reasked_once_without_counting(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", said="..."), dict(stage="open", main="unclear"),
                                         dict(stage="open", main="confirmed"), dict(stage="identity", main="refuses"),
                                         dict(stage="identity", main="refuses")])
    expect(r, "not_verified", "declined")


@pytest.mark.parametrize("main,reason,said", [("wrong_number", "wrong_number", "I'm sorry, you have the wrong number."),
                                              ("deceased", "deceased", "He passed away last year.")])
def test_terminations(spec, api, code, main, reason, said):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", main=main, said=said)])
    expect(r, "not_reached", reason)
    if main == "deceased":
        assert r.agent_texts()[-1].startswith("I am sorry for your loss.")


# ---------- third party and callbacks ----------

def test_day_first_then_time(spec, api, code):
    acts = [dict(stage="open", main="someone_else"), dict(stage="third", main="gives_time", day="tomorrow"),
            dict(stage="third", main="gives_time", time="half past two")]
    r = play(spec, api, code, "PUB-02", acts)
    expect(r, "not_reached", "callback", callbacks=[{"date": "2026-10-15", "time": "14:30"}])


def test_callback_outside_the_window_twice_is_no_callback(spec, api, code):
    acts = [dict(stage="open", main="someone_else"),
            dict(stage="third", main="gives_time", day="tomorrow", time="9 pm"),
            dict(stage="third", main="gives_time", day="Saturday", time="noon")]
    r = play(spec, api, code, "PUB-02", acts)
    expect(r, "not_reached", "no_callback")
    assert not any(c["role"] == "book_callback" for c in r.stub.ledger["calls"])


def test_callback_on_a_friday_rolls_to_monday(spec, api, code):
    acts = [dict(stage="open", main="someone_else"),
            dict(stage="third", main="gives_time", day="tomorrow", time="eleven"),
            dict(stage="third", main="gives_time", day="Monday", time="eleven")]
    r = play(spec, api, code, "PUB-02", acts, now="2026-10-16T11:00")
    expect(r, "not_reached", "callback", callbacks=[{"date": "2026-10-19", "time": "11:00"}])


def test_callback_today_must_be_later_than_now(spec, api, code):
    acts = [dict(stage="open", main="someone_else"),
            dict(stage="third", main="gives_time", day="today", time="10:30"),
            dict(stage="third", main="gives_time", day="today", time="16:45")]
    r = play(spec, api, code, "PUB-02", acts, now="2026-10-14T15:00")
    expect(r, "not_reached", "callback", callbacks=[{"date": "2026-10-14", "time": "16:45"}])


def test_callback_before_a_holiday_skips_it(spec, api, code):
    acts = [dict(stage="open", main="someone_else"),
            dict(stage="third", main="gives_time", day="Monday", time="11:00"),
            dict(stage="third", main="gives_time", day="Tuesday", time="11:00")]
    r = play(spec, api, code, "PUB-02", acts, now="2026-10-23T18:30")
    expect(r, "not_reached", "callback", callbacks=[{"date": "2026-10-27", "time": "11:00"}])


def test_customer_comes_to_the_phone(spec, api, code):
    acts = [dict(stage="open", main="someone_else"), dict(stage="third", main="customer_available"),
            dict(stage="open", main="confirmed"), dict(stage="identity", main="refuses"),
            dict(stage="identity", main="refuses")]
    expect(play(spec, api, code, "PUB-02", acts), "not_verified", "declined")


# ---------- identity ----------

def test_decline_explains_once_then_closes(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", main="confirmed"),
                                         dict(stage="identity", main="refuses", said="I'd rather not."),
                                         dict(stage="identity", side_q="purpose", said="What is this about?")])
    expect(r, "not_verified", "declined")
    assert spec["identity"]["decline"]["explain_template"] in r.agent_texts()[2]


def test_a_swapped_date_gets_the_hint_and_is_not_counted(spec, api, code):
    hint = [{"question": "date_of_birth", "answer": "1988-12-03", "hint": "day_month_swapped"}]
    case = copy.deepcopy(CASES["PUB-01"])
    acts = [dict(stage="open", main="confirmed"),
            dict(stage="identity", main="answers", answer="3/12/1988"),
            dict(stage="identity", main="answers", answer="the third of December 1988"),
            dict(stage="identity", main="answers", answer="12 March 1988"),
            dict(stage="identity", main="answers", answer="14 March 1988"),
            dict(stage="identity", main="answers", answer="4471"),
            dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-01", acts, hints=hint)
    expect(r, "promise", promises=[{"product_id": "p1", "amount": 420.0, "date": "2026-10-14"}])
    assert r.stub.ledger["hinted"] == 2 and r.stub.ledger["wrong"] == 1
    assert spec["identity"]["hints"][0]["say_template"] in r.agent_texts()


def test_hang_up_in_identity_closes_with_the_stage_situation(spec, api, code):
    r = play(spec, api, code, "PUB-01", [dict(stage="open", main="confirmed")])
    sid = spec["closing"]["hangup"]["identity"]["situation"]
    sit = next(s for s in spec["situations"] if s["id"] == sid)
    expect(r, sit["outcome"], sit["reason"] or None)
    assert r.ended == "hangup"


def test_an_unreadable_answer_is_asked_again_without_counting(spec, api, code):
    acts = [dict(stage="open", main="confirmed"), dict(stage="identity", main="answers", answer="March"),
            dict(stage="identity", main="answers", answer="fourteen nineteen eighty eight"),
            dict(stage="identity", main="answers", answer="4471"),
            dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-01", acts)
    expect(r, "promise", promises=[{"product_id": "p1", "amount": 420.0, "date": "2026-10-14"}])
    assert r.stub.ledger["wrong"] == 0


# ---------- the first ask on other days ----------

def test_pay_today_uses_the_day_of_now(spec, api, code):
    r = play(spec, api, code, "PUB-01", ACTS["PUB-01"], now="2026-11-27T09:15")
    expect(r, "promise", promises=[{"product_id": "p1", "amount": 420.0, "date": "2026-11-27"}])


# ---------- the code string ----------

def test_brain_code_is_one_function_without_doubled_braces(code):
    assert brain.lint(code) == []
    assert code.startswith("def _brain(vars):") and code.rstrip().endswith("_brain(vars)")
    assert "# brain: strip" not in code and "from builder" not in code


def test_a_brain_error_is_caught_and_routes_nowhere(spec, api):
    broken = copy.deepcopy(spec)
    broken["situations"] = "not a list"
    vars_ = {"now": "2026-10-14T11:00", "x_main": "unclear", "brain_state": json.dumps(dict(engine.new_state(), asks=2))}
    brain.run_code(brain.source(broken, api), vars_)
    assert vars_["route"] == engine.FAILED and vars_["brain_error"]


@pytest.mark.parametrize("case_id", sorted(ACTS))
def test_engine_imported_and_compiled_agree(spec, api, code, case_id):
    """The imported engine (tests, replay) and the code string (Freya) take the same decisions."""
    from builder import exprs
    S, E = brain.compact_spec(spec, api), exprs.compile_all(spec)
    a = play(spec, api, code, case_id, ACTS[case_id])
    b = play(spec, api, lambda v: engine.run(v, S, E), case_id, ACTS[case_id])
    assert a.agent_texts() == b.agent_texts()
    assert mock_ledger(a.stub) == mock_ledger(b.stub)
