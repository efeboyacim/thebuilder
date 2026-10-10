import copy

import pytest

from builder import bindings as bd
from builder import toolapi as ta
from builder.stubbank import StubBank

CUSTOMER_A = {"identity": {"answers": {"date_of_birth": "1984-03-07", "phone_last4": "4821"}, "limit": 3},
              "products": [{"ref": "p1", "kind": "credit_card", "due_amount": 420.0, "days_overdue": 9,
                            "fields": {"card_last4": "4821", "minimum_payment": 35.0}}]}
CUSTOMER_R = {"identity": {"answers": {"postcode": "AB12CD", "birth_year": "1979"}, "limit": 2},
              "products": [{"ref": "A-1", "kind": "overdraft", "due_amount": 250.5, "days_overdue": 30,
                            "fields": {"overdraft_limit": 1000.0}}]}


# ---------- parsing ----------

def test_parses_the_kit_tool_api(api_a):
    assert api_a.server == "https://freya-bank-mock.freyavoice.workers.dev/v1"
    assert len(api_a.tools) == 7
    v = api_a.tools["verify_identity"]
    assert (v.method, v.path, v.success_status) == ("POST", "/verify_identity", "200")
    assert [p for p, f in v.request.items() if f.required] == ["customer_id", "field", "answer"]
    assert v.request["field"].enum == ["date_of_birth", "phone_last4"]
    assert v.response["hint"].enum == ["day_month_swapped"] and v.response["wrong_answers_left"].type == "integer"
    assert v.error_statuses == {"500": "The service failed. Nothing was recorded. The call may be repeated."}
    g = api_a.tools["get_account"]
    assert g.response["products"].type == "array"
    assert g.response["products[].past_due"].type == "number" and g.response["products[].days_late"].type == "integer"
    p = api_a.tools["create_promise"]
    assert p.request["date"].format == "date" and p.request["amount"].type == "number"
    c = api_a.tools["close_call"]
    assert c.request["outcome"].required and not c.request["reason"].required
    assert api_a.url("close_call") == "https://freya-bank-mock.freyavoice.workers.dev/v1/close_call"


def test_parses_a_renamed_field_copy(api_r):
    assert api_r.server == "https://bank.example.test/api/v2"  # server variable substituted
    v = api_r.tools["verify_identity"]
    assert [p for p, f in v.request.items() if f.required] == ["customer_ref", "question", "value"]  # allOf + $ref
    assert v.request["question"].enum == ["postcode", "birth_year"]
    assert v.response["status.ok"].type == "boolean" and v.response["status.ok"].required
    assert v.response["status.message"].nullable and v.response["status.message"].type == "string"
    assert v.error_statuses == {"503": "The service failed. Nothing was recorded."}  # response $ref
    g = api_r.tools["get_account"]
    assert list(g.request) == ["customer_ref"]
    assert {"accounts[].account_ref", "accounts[].overdue_amount", "accounts[].overdue_days"} <= set(g.response)
    assert api_r.tools["create_promise"].request["amount_cents"].type == "integer"
    assert api_r.tools["schedule_callback"].request["callback_at"].format == "date-time"
    assert api_r.tools["close_call"].request["outcome"].enum == ["PTP", "NOPTP", "NV", "NR", "XFER"]


def test_summary_text_lists_every_tool_and_field(api_a, api_r):
    for api in (api_a, api_r):
        text = ta.summary_text(api)
        for t in api.tools.values():
            assert f"tool {t.name} ({t.method} {t.path})" in text
            for f in list(t.request.values()) + list(t.response.values()):
                assert f.describe() in text


def test_operation_id_names_the_tool():
    data = {"openapi": "3.0.0", "paths": {"/v1/x/do": {"post": {"operationId": "doIt", "responses": {}}}}}
    api = ta.parse(data)
    assert list(api.tools) == ["doIt"] and api.tools["doIt"].request == {} and api.server == ""


@pytest.mark.parametrize("doc,msg", [
    ({"swagger": "2.0"}, "not an openapi"),
    ({"openapi": "3.0.0", "paths": {}}, "no operations"),
    ({"openapi": "3.0.0", "paths": {"/a": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"$ref": "other.json#/X"}}}}}}}}, "external"),
    ({"openapi": "3.0.0", "components": {"schemas": {"N": {"type": "object", "properties": {
        "next": {"$ref": "#/components/schemas/N"}}}}}, "paths": {"/a": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"$ref": "#/components/schemas/N"}}}}}}}}, "too deep"),
    ({"openapi": "3.0.0", "paths": {"/a": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/Missing"}}}}}}}}, "not found"),
])
def test_unusable_documents(doc, msg):
    with pytest.raises(ta.ToolAPIError, match=msg):
        ta.parse(doc)


# ---------- request validation ----------

def test_validate_body(api_a, api_r):
    p = api_a.tools["create_promise"]
    good = {"customer_id": "c1", "product_id": "p1", "amount": 420.0, "date": "2026-10-16"}
    assert ta.validate_body(p, good) == []
    assert ta.validate_body(p, {**good, "extra": 1}) == []  # extra keys allowed unless the schema forbids them
    assert ta.unknown_fields(p, {**good, "extra": 1}) == ["extra"]
    errs = ta.validate_body(p, {k: v for k, v in good.items() if k != "amount"})
    assert errs == ["body.amount: required field missing"]
    assert "not a valid date" in ta.validate_body(p, {**good, "date": "2026-02-30"})[0]
    assert "expected number" in ta.validate_body(p, {**good, "amount": "420"})[0]
    assert "expected number" in ta.validate_body(p, {**good, "amount": True})[0]
    v = api_a.tools["verify_identity"]
    assert "is not one of" in ta.validate_body(v, {"customer_id": "c", "field": "postcode", "answer": "x"})[0]
    r = api_r.tools["create_promise"]
    rgood = {"customer_ref": "c", "account_ref": "A-1", "amount_cents": 25050, "pay_on": "2026-10-16"}
    assert ta.validate_body(r, rgood) == []
    assert "expected integer" in ta.validate_body(r, {**rgood, "amount_cents": 250.5})[0]
    assert ta.validate_body(r, {**rgood, "amount_cents": 25050.0}) == []
    assert "minimum" in ta.validate_body(r, {**rgood, "amount_cents": 0})[0]
    assert "not in the schema" in ta.validate_body(r, {**rgood, "note": "x"})[0]
    cb = api_r.tools["schedule_callback"]
    assert ta.validate_body(cb, {"customer_ref": "c", "callback_at": "2026-10-15T10:30:00"}) == []
    assert "date-time" in ta.validate_body(cb, {"customer_ref": "c", "callback_at": "2026-10-15"})[0]


def test_examples_fit_their_schemas(api_a, api_r):
    for api in (api_a, api_r):
        for t in api.tools.values():
            if t.request_schema:
                assert ta.check(t.request_schema, ta.example(t.request_schema)) == []
            if t.response_schema:
                assert ta.check(t.response_schema, ta.example(t.response_schema), "response") == []


# ---------- acceptance: the stub rejects a body missing a required field ----------

@pytest.mark.parametrize("which", ["a", "r"])
def test_stub_rejects_a_body_missing_a_required_field(which, api_a, api_r, b_a, b_r):
    api, b, customer = (api_a, b_a, CUSTOMER_A) if which == "a" else (api_r, b_r, CUSTOMER_R)
    stub = StubBank(api, b, copy.deepcopy(customer))
    tool = api.tools[b.tools["verify"]]
    question, answer = next(iter(customer["identity"]["answers"].items()))
    body = bd.render_body(b, "verify", {"question": question, "answer": answer}, {b.call_vars["customer_key"]: "c1"})
    missing = next(p for p, f in tool.request.items() if f.required and b.request["verify"][p]["source"] == "engine")
    bad = {k: v for k, v in body.items() if k != missing}
    r = stub.call(tool.name, bad)
    assert r.status == 400
    assert stub.ledger["calls"][-1]["rejected"] == [f"body.{missing}: required field missing"]
    assert stub.ledger["wrong"] == 0 and stub.passed == set()
    assert stub.call(tool.name, body).status == 200 and stub.passed == {question}
