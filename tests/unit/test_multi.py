"""Several products (item 14) on the Brief A+B fixture spec, through the brain code string and the stub bank: one
first ask for all, products one by one from attempt 1, one day for all with each product's own deadline, one read-back
and one confirmation, notes for every product without a promise, events that end one product, the closing
aggregation, transfers without notes, and a promise that fails twice among several. Single-product calls under the
B rules get their notes too (tests/cases/a-under-b.json).
"""
import pytest

from builder import bindings as bd
from builder import brain, replay, toolapi
from builder.stubbank import StubBank
from tests.helpers import FIX, ROOT, load, mock_ledger
from tests.unit.test_engine_slice import CASES, customer, score


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    return load(FIX / "spec-ab-v1.json")


@pytest.fixture(scope="module")
def code(spec, api):
    return brain.source(spec, api)


def play(spec, api, code, case_id, acts, now=None, faults=None):
    case = CASES[case_id]
    stub = StubBank(api, bd.Bindings.from_dict(spec["bindings"]), customer(case), faults=faults)
    variables = dict(case["variables"], customer_id="c_" + case_id.lower())
    if now:
        variables["now"] = now
    return replay.Replay(spec, api, stub, variables, code=code, max_hops=20).run(acts)


def verified(case_id):
    ids = CASES[case_id]["identity"]
    return [dict(stage="open", main="confirmed"), dict(stage="identity", main="answers", answer=ids["date_of_birth"]),
            dict(stage="identity", main="answers", answer=ids["phone_last4"])]


def expect(r, outcome, reason=None, promises=(), notes=(), transfers=()):
    case = {"expected": {"outcome": outcome, "reason": reason, "promises": [
        {"product_id": p, "amount": a, "date": d} for p, a, d in promises],
        "notes": [{"product_id": p, "reason": n} for p, n in notes], "callbacks": [], "transfers": list(transfers)}}
    res = score.score_ledger(case, mock_ledger(r.stub))
    assert res["pass"], (res["failed"], r.agent_texts())
    assert r.stub.ledger["violations"] == []


def passes(r, case_id):
    res = score.score_ledger(CASES[case_id], mock_ledger(r.stub))
    assert res["pass"], (res["failed"], r.agent_texts())


def after_disclosure(r):
    texts = r.agent_texts()
    return texts[next(n for n, t in enumerate(texts) if "collections" in t):]


# ---------- acceptance scripts ----------

def test_pub11_one_date_for_all_three(spec, api, code):
    acts = verified("PUB-11") + [
        dict(stage="negotiate", main="refuses", said="No, I can't pay today."),
        dict(stage="negotiate", main="proposes", scope="all", day="this Friday", said="I'll pay all three this Friday."),
        dict(stage="confirm", main="yes", said="Yes.")]
    r = play(spec, api, code, "PUB-11", acts)
    passes(r, "PUB-11")
    said = after_disclosure(r)
    first = said[0]
    assert all(x in first for x in ("credit card ending 2288", "personal loan", "car loan"))
    assert first.endswith("Together that is 935. Can you pay all of them today?")
    assert said[1] == "About your credit card ending 2288: Could you pay 220 on Friday, 16 October?"
    assert said[2].count("Friday, 16 October") == 3 and said[2].endswith("Do you confirm?")
    assert [c["role"] for c in r.stub.ledger["calls"]].count("record_promise") == 3


def test_pub12_one_promise_two_refused(spec, api, code):
    acts = [dict(stage="open", main="confirmed"),
            dict(stage="identity", main="answers", answer="1979-08-30"),
            dict(stage="identity", main="answers", answer="4401"),
            dict(stage="identity", main="answers", answer="4410"),
            dict(stage="negotiate", main="proposes", scope="one", product="the credit card", day="tomorrow",
                 said="I'll pay the credit card tomorrow, but not the others."),
            dict(stage="negotiate", main="refuses"), dict(stage="negotiate", main="refuses"),
            dict(stage="negotiate", main="refuses"), dict(stage="confirm", main="yes")]
    case = CASES["PUB-12"]
    acts[1]["answer"] = case["identity"]["date_of_birth"]
    r = play(spec, api, code, "PUB-12", acts)
    passes(r, "PUB-12")
    said = after_disclosure(r)
    assert said[1] == "About your personal loan: Could you pay 1320 on Friday, 16 October?"
    assert said[2] == "Could you pay 440 on Friday, 23 October? The rest stays overdue."
    assert said[3] == "About your car loan: Could you pay 140 on Friday, 16 October?"
    assert "credit card ending 9015 on Thursday, 15 October" in said[4]


def test_pub13_one_date_after_one_deadline(spec, api, code):
    acts = verified("PUB-13") + [
        dict(stage="negotiate", main="refuses", scope="all", day="30 October", said="No, I'll pay both on 30 October."),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-13", acts)
    passes(r, "PUB-13")
    readback = after_disclosure(r)[1]
    assert "Friday, 30 October" in readback and "Friday, 23 October" in readback
    assert "That is the latest date I can record for your personal loan." in readback


def test_accepting_all_today(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-13", acts), "promise",
           promises=[("p1", 300.0, "2026-10-14"), ("p2", 900.0, "2026-10-14")])


# ---------- notes and the closing aggregation ----------

def test_everything_refused_notes_every_product(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", main="refuses")] * 4
    r = play(spec, api, code, "PUB-13", acts)
    expect(r, "no_promise", "refused", notes=[("p1", "refused"), ("p2", "refused")])
    assert [c["role"] for c in r.stub.ledger["calls"]][-3:] == ["record_note", "record_note", "close"]


def test_all_paid_closes_as_paid(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", event="paid", product="the card", said="I paid the card."),
                                 dict(stage="negotiate", event="paid", said="And I paid the loan yesterday.")]
    r = play(spec, api, code, "PUB-13", acts)
    expect(r, "paid", notes=[("p1", "paid"), ("p2", "paid")])
    assert after_disclosure(r)[1].startswith("Thank you for letting me know. Payments can take one business day")


def test_paid_and_refused_closes_with_the_first_note_that_is_not_paid(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", event="paid", product="the card"),
                                 dict(stage="negotiate", main="refuses"), dict(stage="negotiate", main="refuses")]
    expect(play(spec, api, code, "PUB-13", acts), "no_promise", "refused", notes=[("p1", "paid"), ("p2", "refused")])


def test_a_dispute_on_the_second_product_continues(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", main="proposes", scope="one", product="card", day="Friday"),
                                 dict(stage="negotiate", event="dispute", said="That loan is not mine."),
                                 dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-13", acts), "promise", promises=[("p1", 300.0, "2026-10-16")],
           notes=[("p2", "dispute")])


def test_dispute_first_then_refused(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", event="dispute", product="the credit card"),
                                 dict(stage="negotiate", main="refuses"), dict(stage="negotiate", main="refuses")]
    expect(play(spec, api, code, "PUB-13", acts), "no_promise", "dispute",
           notes=[("p1", "dispute"), ("p2", "refused")])


def test_hardship_with_several_products_notes_all(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", event="hardship", said="I lost my job."),
                                 dict(stage="negotiate", reply="no")]
    expect(play(spec, api, code, "PUB-13", acts), "no_promise", "hardship",
           notes=[("p1", "hardship"), ("p2", "hardship")])


def test_restructuring_drops_agreed_promises_and_writes_no_notes(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", main="proposes", scope="one", product="card", day="Friday"),
                                 dict(stage="negotiate", event="restructuring", said="For the loan I need lower instalments.")]
    r = play(spec, api, code, "PUB-13", acts)
    expect(r, "transferred", "restructuring", transfers=[{"queue": "restructuring"}])
    assert not any(c["role"] in ("record_promise", "record_note") for c in r.stub.ledger["calls"])


def test_a_promise_failing_twice_among_several_keeps_the_others(spec, api, code):
    acts = verified("PUB-11") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-11", acts, faults={"record_promise": ["500", "500"]})
    expect(r, "promise", promises=[("p2", 640.0, "2026-10-14"), ("p3", 75.0, "2026-10-14")],
           notes=[("p1", "service_error")])
    assert "credit card ending 2288" not in r.agent_texts()[-1]
    assert "personal loan" in r.agent_texts()[-1] and "car loan" in r.agent_texts()[-1]


def test_every_promise_failing_closes_as_service_error(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-13", acts, faults={"record_promise": ["500"] * 4})
    expect(r, "no_promise", "service_error", notes=[("p1", "service_error"), ("p2", "service_error")])
    assert "is recorded" not in r.agent_texts()[-1]


# ---------- one product under the B rules ----------

def test_one_product_hardship_gets_its_note(spec, api, code):
    acts = verified("PUB-05") + [dict(stage="negotiate", event="hardship"), dict(stage="negotiate", reply="no")]
    expect(play(spec, api, code, "PUB-05", acts), "no_promise", "hardship", notes=[("p1", "hardship")])


def test_one_product_paid_gets_its_note(spec, api, code):
    acts = verified("PUB-08") + [dict(stage="negotiate", event="paid")]
    expect(play(spec, api, code, "PUB-08", acts), "paid", notes=[("p1", "paid")])


def test_one_product_promise_writes_no_note(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-01", acts)
    expect(r, "promise", promises=[("p1", 420.0, "2026-10-14")])
    assert not any(c["role"] == "record_note" for c in r.stub.ledger["calls"])


def test_a_under_b_cases_file_matches_the_public_cases():
    cases = {c["id"]: c for c in load(ROOT / "tests" / "cases" / "a-under-b.json")}
    pub = {c["id"]: c for c in load(ROOT / "public-test-set.json") if c["brief"] == "A"}
    assert set(cases) == set(pub)
    for cid, c in cases.items():
        want = dict(pub[cid]["expected"])
        if cid == "PUB-05":
            want["notes"] = [{"product_id": "p1", "reason": "hardship"}]
        if cid == "PUB-08":
            want["notes"] = [{"product_id": "p1", "reason": "paid"}]
        assert c["expected"] == want and c["script"] == pub[cid]["script"]


def test_a_day_without_scope_at_the_first_ask_is_for_the_current_product(spec, api, code):
    acts = verified("PUB-12") + [dict(stage="negotiate", main="proposes", day="tomorrow",
                                      said="I will pay the credit card tomorrow."),
                                 dict(stage="negotiate", main="refuses"), dict(stage="negotiate", main="refuses"),
                                 dict(stage="negotiate", main="refuses"), dict(stage="confirm", main="yes")]
    passes(play(spec, api, code, "PUB-12", acts), "PUB-12")


def test_both_in_what_was_said_is_for_all(spec, api, code):
    acts = verified("PUB-13") + [dict(stage="negotiate", main="refuses", day="30 October",
                                      said="No, I will pay both on 30 October."), dict(stage="confirm", main="yes")]
    passes(play(spec, api, code, "PUB-13", acts), "PUB-13")


def test_a_correction_at_a_read_back_of_several(spec, api, code):
    acts = verified("PUB-12") + [
        dict(stage="negotiate", main="proposes", scope="all", day="tomorrow", said="All of them tomorrow."),
        dict(stage="confirm", main="no", product="the credit card", day="tomorrow",
             said="No, only the credit card tomorrow, not the others."),
        dict(stage="negotiate", main="refuses"), dict(stage="negotiate", main="refuses"),
        dict(stage="negotiate", main="refuses"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-12", acts)
    passes(r, "PUB-12")


def test_all_on_the_day_the_agent_proposed(spec, api, code):
    acts = verified("PUB-11") + [dict(stage="negotiate", main="refuses"),
                                 dict(stage="negotiate", main="accepts", scope="all", day="that day",
                                      said="I will pay all three on that day."),
                                 dict(stage="confirm", main="yes")]
    passes(play(spec, api, code, "PUB-11", acts), "PUB-11")
