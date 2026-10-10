import copy

import pytest

from builder import bindings as bd
from builder import toolapi as ta
from builder.stubbank import StubBank, StubError

CALLS = {"customer_id": "c1", "customer_ref": "c1", "first_name": "Ann", "last_name": "Lee", "title": "Ms"}
CUSTOMER_A = {
    "identity": {"answers": {"date_of_birth": "1984-03-07", "phone_last4": "4821"}, "limit": 3,
                 "hints": [{"question": "date_of_birth", "answer": "1984-07-03", "hint": "day_month_swapped"}],
                 "readable": {"date_of_birth": r"^\d{4}-\d{2}-\d{2}$"}},
    "products": [{"ref": "p1", "kind": "credit_card", "due_amount": 420.0, "days_overdue": 9,
                  "fields": {"card_last4": "4821", "minimum_payment": 35.0}},
                 {"ref": "p2", "kind": "car_loan", "due_amount": 610.0, "days_overdue": 3,
                  "fields": {"instalment_amount": 305.0}}]}
CUSTOMER_R = {"identity": {"answers": {"postcode": "AB12CD", "birth_year": "1979"}, "limit": 2,
                           "hints": [{"question": "postcode", "answer": "AB12", "hint": "partial_postcode"}]},
              "products": [{"ref": "A-1", "kind": "overdraft", "due_amount": 250.5, "days_overdue": 30,
                            "fields": {"overdraft_limit": 1000.0}}]}
CLOSE_RULES_A = {"promise": [], "paid": [], "no_promise": ["refused", "service_error"], "not_reached": ["unclear"]}


class Caller:
    """Calls the stub the way the engine would: engine roles in, response roles out."""

    def __init__(self, api, b, customer, **kw):
        self.api, self.b = api, b
        self.stub = StubBank(api, b, copy.deepcopy(customer), **kw)

    def __call__(self, role, **engine):
        body = bd.render_body(self.b, role, engine, CALLS)
        r = self.stub.call(self.b.tools[role], body)
        tool = self.api.tools[self.b.tools[role]]
        if r.status == 200:
            assert ta.check(tool.response_schema, r.body, "response") == []
        return r.status, (bd.read_response(self.b, role, r.body) if r.body is not None else None)


@pytest.fixture
def a(api_a, b_a):
    return Caller(api_a, b_a, CUSTOMER_A, close_rules=CLOSE_RULES_A)


@pytest.fixture
def r(api_r, b_r):
    return Caller(api_r, b_r, CUSTOMER_R)


def verify_all(c, answers):
    for q, ans in answers.items():
        status, v = c("verify", question=q, answer=ans)
        assert status == 200 and v["ok"] and v["verified"]
    return v


def test_happy_path_records_a_promise_and_closes(a):
    status, v = a("lookup")
    assert v["ok"] is False and "before identity" in a.stub.ledger["violations"][0]
    v = verify_all(a, CUSTOMER_A["identity"]["answers"])
    assert v["all_verified"] is True and a.stub.ledger["verified"]
    _, look = a("lookup")
    assert [(p["ref"], p["kind"], p["due_amount"], p["days_overdue"]) for p in look["products"]] == [
        ("p1", "credit_card", 420.0, 9), ("p2", "car_loan", 610.0, 3)]
    assert look["products"][0]["raw"]["minimum_payment"] == 35.0
    _, p = a("record_promise", product_ref="p1", amount=420, date="2026-10-16")
    assert p["ok"] and p["promise_ref"] == "pr_1"
    _, c = a("close", outcome="promise")
    assert c["ok"]
    led = a.stub.ledger
    assert led["promises"] == [("p1", 420.0, "2026-10-16")] and (led["outcome"], led["reason"]) == ("promise", None)
    _, after = a("transfer", queue="general")
    assert after["ok"] is False and "after the call was closed" in led["violations"][-1]


def test_wrong_answers_count_down_and_lock(a):
    for left in (2, 1, 0):
        _, v = a("verify", question="phone_last4", answer="0000")
        assert v["ok"] and v["verified"] is False and v["attempts_left"] == left
    _, v = a("verify", question="phone_last4", answer="4821")
    assert v["ok"] is False and v["error"] == "too many wrong answers"
    assert a.stub.ledger["wrong"] == 3 and a.stub.passed == set()


def test_hint_and_unreadable_answers_are_not_counted(a):
    _, v = a("verify", question="date_of_birth", answer="1984-07-03")
    assert v["ok"] and v["verified"] is False and v["hint"] == "day_month_swapped" and v["attempts_left"] == 3
    _, v = a("verify", question="date_of_birth", answer="seventh of March")
    assert v["ok"] is False and v["error"] == "unreadable answer"
    assert (a.stub.ledger["wrong"], a.stub.ledger["hinted"], a.stub.ledger["unreadable"]) == (0, 1, 1)


def test_500_then_retry_records_once(api_a, b_a):
    c = Caller(api_a, b_a, CUSTOMER_A, faults={"record_promise": ["500", "none", "ok_false", "timeout"]})
    verify_all(c, CUSTOMER_A["identity"]["answers"])
    status, v = c("record_promise", product_ref="p1", amount=420, date="2026-10-16")
    assert status == 500 and v["ok"] is False and "nothing was recorded" in v["error"]
    assert c.stub.ledger["promises"] == []
    status, v = c("record_promise", product_ref="p1", amount=420, date="2026-10-16")
    assert status == 200 and v["ok"] and c.stub.ledger["promises"] == [("p1", 420.0, "2026-10-16")]
    status, v = c("record_promise", product_ref="p2", amount=305, date="2026-10-16")
    assert status == 200 and v["ok"] is False
    status, v = c("record_promise", product_ref="p2", amount=305, date="2026-10-16")
    assert status == 408 and v is None
    _, v = c("record_promise", product_ref="p1", amount=100, date="2026-10-17")
    assert v["ok"] is False and "second promise" in c.stub.ledger["violations"][-1]
    _, v = c("record_promise", product_ref="p9", amount=100, date="2026-10-17")
    assert v["ok"] is False and "unknown product" in c.stub.ledger["violations"][-1]
    assert [e["fault"] for e in c.stub.ledger["calls"] if e["role"] == "record_promise"][:4] == [
        "500", "none", "ok_false", "timeout"]


def test_close_rules_like_the_outcome_table(a):
    _, v = a("close", outcome="no_promise", reason="hardship")
    assert v["ok"] is False and a.stub.ledger["outcome"] is None and not a.stub.closed
    _, v = a("close", outcome="paid", reason="refused")
    assert v["ok"] is False
    _, v = a("close", outcome="not_reached", reason="unclear")
    assert v["ok"] and (a.stub.ledger["outcome"], a.stub.ledger["reason"]) == ("not_reached", "unclear")


def test_writes_are_recorded_in_engine_terms(a):
    verify_all(a, CUSTOMER_A["identity"]["answers"])
    a("record_note", product_ref="p2", note_reason="dispute")
    a("book_callback", date="2026-10-15", time="10:30")
    a("transfer", queue="support")
    led = a.stub.ledger
    assert led["notes"] == [("p2", "dispute")] and led["callbacks"] == [("2026-10-15", "10:30")]
    assert led["transfers"] == ["support"]


def test_renamed_api_cents_joined_datetime_and_nested_ok(r):
    _, v = r("verify", question="postcode", answer="AB12")
    assert v["ok"] and v["hint"] == "partial_postcode" and v["attempts_left"] == 2
    _, v = r("verify", question="postcode", answer="ab12cd")  # compared without case
    assert v["verified"] and v["all_verified"] is False
    _, v = r("verify", question="birth_year", answer="1979")
    assert v["all_verified"] is True
    _, look = r("lookup")
    assert look["products"][0]["ref"] == "A-1" and look["products"][0]["raw"]["overdraft_limit"] == 1000.0
    _, p = r("record_promise", product_ref="A-1", amount=250.5, date="2026-10-16")
    assert p["ok"] and p["promise_ref"] == 1  # an integer field gets an integer id
    assert r.stub.ledger["calls"][-1]["body"]["amount_cents"] == 25050
    assert r.stub.ledger["promises"] == [("A-1", 250.5, "2026-10-16")]
    r("book_callback", date="2026-10-15", time="14:00")
    assert r.stub.ledger["callbacks"] == [("2026-10-15", "14:00")]
    r("record_note", product_ref="A-1", note_reason="R2")
    assert r.stub.ledger["calls"][-1]["body"]["text"] == "Call with Ann Lee: R2"


def test_invalid_body_on_renamed_api(r, api_r):
    resp = r.stub.call("create_promise", {"customer_ref": "c1", "account_ref": "A-1", "amount_cents": 250.5,
                                          "pay_on": "2026-10-16"})
    assert resp.status == 400 and resp.body["status"]["ok"] is False
    assert "expected integer" in r.stub.ledger["calls"][-1]["rejected"][0]


def test_unshaped_response_is_a_stub_error(api_a, b_a):
    bad = copy.deepcopy(CUSTOMER_A)
    bad["identity"]["hints"][0]["hint"] = "not_an_enum_value"
    c = Caller(api_a, b_a, bad)
    with pytest.raises(StubError, match="does not fit the schema"):
        c("verify", question="date_of_birth", answer="1984-07-03")


def test_unknown_fault_kind_is_rejected(api_a, b_a):
    with pytest.raises(StubError, match="unknown fault"):
        StubBank(api_a, b_a, CUSTOMER_A, faults={"record_promise": ["503"]})
