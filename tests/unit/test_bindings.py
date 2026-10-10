import copy, datetime

import pytest

from builder import bindings as bd
from builder import toolapi as ta

CALLS_A = {"customer_id": "c1", "now": "2026-10-14T11:00", "first_name": "Ann", "last_name": "Lee", "title": "Ms"}
CALLS_R = {"customer_ref": "c1", "now": "2026-10-14T11:00", "first_name": "Ann", "last_name": "Lee"}
ENGINE = {
    "verify": {"question": None, "answer": "x"},
    "lookup": {},
    "record_promise": {"product_ref": None, "amount": 250.5, "date": datetime.date(2026, 10, 16)},
    "record_note": {"product_ref": None, "note_reason": None},
    "book_callback": {"date": "2026-10-15", "time": "9:30"},
    "transfer": {"queue": None},
    "close": {"outcome": None, "reason": "x"},
}


def engine_for(api, b):
    """Engine values with the API's own enum values and ids filled in."""
    e = copy.deepcopy(ENGINE)
    for role, values in e.items():
        tool = api.tools[b.tools[role]]
        for r, (path, _) in b.engine_fields(role).items():
            if r in values and values[r] is None:
                values[r] = (tool.request[path].enum or ["ref-1"])[0]
    return e


def test_fixture_bindings_are_valid(api_a, api_r, b_a, b_r):
    assert bd.validate(b_a, api_a, call_vars=CALLS_A) == []
    assert bd.validate(b_r, api_r, call_vars=CALLS_R) == []


def test_bindings_for_one_api_fail_on_the_renamed_copy(api_r, b_a):
    errs = bd.validate(b_a, api_r)
    assert "request.verify.customer_id: no such body field in verify_identity (customer_ref, question, value)" in errs
    assert "request.record_promise.amount_cents: required body field of create_promise has no binding" in errs
    assert "request.record_promise: engine role 'amount' reaches no body field" in errs
    assert any(e.startswith("response.lookup.products: no response field 'products'") for e in errs)


def mutate(b, fn):
    d = copy.deepcopy(b.to_dict())
    fn(d)
    return bd.Bindings.from_dict(d)


@pytest.mark.parametrize("fn,expected", [
    (lambda d: d["tools"].update(pay="create_promise"), "tools.pay: unknown tool role"),
    (lambda d: d["tools"].update(verify="check_identity"), "tools.verify: no tool 'check_identity'"),
    (lambda d: d["tools"].update(lookup="verify_identity"), "is already bound to verify"),
    (lambda d: d["request"]["record_promise"].pop("pay_on"), "request.record_promise.pay_on: required body field"),
    (lambda d: d["request"]["record_promise"]["amount_cents"].pop("convert"),
     "engine role 'amount' gives a number, the field is integer (use cents_int)"),
    (lambda d: d["request"]["record_promise"]["amount_cents"].update(convert="number_2dp"),
     "number_2dp gives number, the field is integer"),
    (lambda d: d["request"]["record_promise"]["pay_on"].update(convert="time_hhmm"),
     "time_hhmm needs the engine role 'time'"),
    (lambda d: d["request"]["record_promise"]["pay_on"].update(convert="weekday"), "unknown converter"),
    (lambda d: d["request"]["book_callback"]["callback_at"].update(value=["date", "queue"]),
     "engine role ['queue'] is not one of date, time"),
    (lambda d: d["request"]["book_callback"]["callback_at"].pop("convert"),
     "several engine roles need the datetime_join converter"),
    (lambda d: d["request"]["transfer"]["channel"].update(value="email"), "literal 'email' is not one of ['voice']"),
    (lambda d: d["request"]["record_note"]["text"].update(value="{{note_reason}} on {{balance}}"),
     "template variable 'balance' is not a role"),
    (lambda d: d["request"]["close"]["outcome"].update(source="model"), "source must be one of"),
    (lambda d: d["request"]["verify"]["question"].update(source="call_var", value="question"),
     "call-variable role 'question' is not bound"),
    (lambda d: d["request"]["verify"].pop("value"), "engine role 'answer' reaches no body field"),
    (lambda d: d["request"]["verify"]["status"] if False else d["request"].update(pay={"x": {}}),
     "pay: bound fields but no tool"),
    (lambda d: d["response"]["verify"].update(verified="status.passed"), "no response field 'status.passed'"),
    (lambda d: d["response"]["verify"].update(attempts_left="all_passed"),
     "all_passed is boolean, the role needs integer/number"),
    (lambda d: d["response"]["verify"].update(score="passed"), "response.verify.score: unknown response role"),
    (lambda d: d["response"]["lookup"]["item"].pop("ref"), "response.lookup.item.ref: the product id has no binding"),
    (lambda d: d["response"]["lookup"]["item"].update(due_amount="overdue"), "no field 'overdue' in the items of accounts"),
    (lambda d: d["response"]["lookup"]["item"].update(kind="overdue_days"), "overdue_days is integer, the role needs string"),
    (lambda d: d["call_vars"].update(customer_key="customer_id"), "'customer_id' is not a call variable"),
    (lambda d: d["call_vars"].update(nickname="first_name"), "call_vars.nickname: unknown role"),
])
def test_each_binding_mistake_is_reported(api_r, b_r, fn, expected):
    errs = bd.validate(mutate(b_r, fn), api_r, call_vars=CALLS_R)
    assert any(expected in e for e in errs), errs


def test_converters():
    assert bd.number_2dp(12.345) == 12.35 and bd.number_2dp(420) == 420.0
    assert bd.cents_int(19.995) == 2000 and bd.cents_int(250.5) == 25050 and bd.cents_int(0.1 + 0.2) == 30
    assert bd.string_2dp(420) == "420.00" and bd.string_2dp(2.675) == "2.68"
    assert bd.date_iso(datetime.date(2026, 1, 2)) == "2026-01-02" == bd.date_iso("2026-01-02T10:00")
    assert bd.time_hhmm("9:05") == "09:05" == bd.time_hhmm(datetime.time(9, 5)) == bd.time_hhmm("09.05")
    assert bd.datetime_join("2026-10-15", "14:30") == "2026-10-15T14:30:00"
    for bad in ("25:00", "noon", "9"):
        with pytest.raises(bd.BindingError):
            bd.time_hhmm(bad)


@pytest.mark.parametrize("which", ["a", "r"])
def test_rendered_bodies_fit_the_schema_and_parse_back(which, api_a, api_r, b_a, b_r):
    api, b, calls = (api_a, b_a, CALLS_A) if which == "a" else (api_r, b_r, CALLS_R)
    engine = engine_for(api, b)
    for role, values in engine.items():
        tool = api.tools[b.tools[role]]
        body = bd.render_body(b, role, values, calls)
        assert ta.validate_body(tool, body) == [], (role, body)
        back = bd.parse_body(b, role, body)
        for r, v in values.items():
            if r not in back:
                continue
            want = {"date": "2026-10-16" if role == "record_promise" else "2026-10-15", "time": "09:30"}.get(r, v)
            assert back[r] == want, (role, r)
    if which == "r":
        assert bd.render_body(b, "record_promise", engine["record_promise"], calls)["amount_cents"] == 25050
        assert bd.render_body(b, "book_callback", engine["book_callback"], calls) == {
            "customer_ref": "c1", "callback_at": "2026-10-15T09:30:00"}
        note = bd.render_body(b, "record_note", engine["record_note"], calls)
        assert note["text"] == "Call with Ann Lee: R1" and note["code"] == "R1"
        assert bd.render_body(b, "transfer", engine["transfer"], calls)["channel"] == "voice"


def test_unset_optional_values_are_left_out(api_a, b_a):
    body = bd.render_body(b_a, "close", {"outcome": "paid"}, CALLS_A)
    assert body == {"customer_id": "c1", "outcome": "paid"}
    assert ta.validate_body(api_a.tools["close_call"], body) == []


def test_response_roles_round_trip(api_r, b_r):
    body = bd.build_response(b_r, "verify", {"ok": True, "verified": False, "attempts_left": 1, "hint": "partial_postcode"})
    assert body == {"status": {"ok": True}, "passed": False, "attempts_remaining": 1, "hint": "partial_postcode"}
    assert ta.check(api_r.tools["verify_identity"].response_schema, body) == []
    assert bd.read_response(b_r, "verify", body) == {"ok": True, "error": None, "verified": False, "all_verified": None,
                                                     "attempts_left": 1, "hint": "partial_postcode"}
    items = [{"ref": "A-1", "kind": "overdraft", "due_amount": 250.5, "days_overdue": 30, "fields": {"overdraft_limit": 1e3}}]
    body = bd.build_response(b_r, "lookup", {"ok": True}, items)
    assert body["accounts"] == [{"overdraft_limit": 1000.0, "account_ref": "A-1", "category": "overdraft",
                                 "overdue_amount": 250.5, "overdue_days": 30}]
    (p,) = bd.read_response(b_r, "lookup", body)["products"]
    assert (p["ref"], p["kind"], p["due_amount"], p["days_overdue"]) == ("A-1", "overdraft", 250.5, 30)
    assert p["raw"]["overdraft_limit"] == 1000.0
