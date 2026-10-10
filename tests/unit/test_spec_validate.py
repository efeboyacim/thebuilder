"""Deterministic spec validation on the Brief A v0 fixture: each seeded defect is reported with its path."""
import copy

import pytest

from builder import specgen, toolapi
from builder import spec as sp
from tests.helpers import FIX, ROOT, load

BRIEF = (ROOT / "briefs" / "brief-a.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture
def spec():
    return load(FIX / "spec-a-v1.json")


@pytest.fixture(scope="module")
def ids():
    d = load(FIX / "digest-a-v0.json")
    return specgen.binding_rule_ids(d), [r["id"] for r in d["rules"]]


def errors(spec, api, ids):
    return sp.validate(spec, api, BRIEF, *ids).errors


def test_fixture_is_clean(spec, api, ids):
    r = sp.validate(spec, api, BRIEF, *ids)
    assert r.errors == [] and r.warnings == []
    assert r.info["identity_questions"] and r.info["situations"] >= 10


def _sit(spec, outcome, reason):
    return next(i for i, s in enumerate(spec["situations"]) if s["outcome"] == outcome and s["reason"] == reason)


def test_unknown_outcome_and_reason(spec, api, ids):
    i = next(i for i, s in enumerate(spec["situations"]) if s["reason"])
    spec["situations"][i]["outcome"] = "maybe"
    errs = errors(spec, api, ids)
    assert any(e.startswith(f"situations[{i}].outcome: 'maybe' is not one of the tool's values") for e in errs)
    assert any("no situation for outcome" in e for e in errs)


def test_free_text_code_must_occur_in_brief(spec, api, ids):
    i = next(i for i, s in enumerate(spec["situations"]) if s["reason"])
    spec["situations"][i]["reason"] = "invented_reason"  # the close tool's reason field has no enum
    errs = errors(spec, api, ids)
    assert any(e.startswith(f"situations[{i}].reason: 'invented_reason' does not occur in the brief") for e in errs)


def test_placeholders(spec, api, ids):
    spec["opening"]["template"] = spec["opening"]["template"] + " {nickname} {{due_amount}}"
    errs = errors(spec, api, ids)
    assert any(e.startswith("opening.template: write the placeholder {nickname} as {{nickname}}") for e in errs)
    assert any(e.startswith("opening.template: placeholder {{due_amount}} is not allowed here") for e in errs)


def test_forbidden_word_before_verification(spec, api, ids):
    word = spec["opening"]["forbidden_before_verification"][0]
    spec["identity"]["decline"]["explain_template"] += f" It is about your {word}."
    errs = errors(spec, api, ids)
    assert f"identity.decline.explain_template: says {word!r}, which is forbidden before identity is verified" in errs


def test_forbidden_word_in_a_pre_verification_situation(spec, api, ids):
    sid = spec["identity"]["on_failed"]
    i = next(i for i, s in enumerate(spec["situations"]) if s["id"] == sid)
    word = spec["opening"]["forbidden_before_verification"][-1]
    spec["situations"][i]["say"] = f"Your {word} is overdue."
    assert any(e.startswith(f"situations[{i}].say: says {word!r}") for e in errors(spec, api, ids))


def test_situation_reference_and_duplicate(spec, api, ids):
    spec["identity"]["on_failed"] = "no_such_situation"
    spec["situations"].append(copy.deepcopy(spec["situations"][0]))
    errs = errors(spec, api, ids)
    assert "identity.on_failed: 'no_such_situation' is not a situation id" in errs
    assert any(e.startswith("situations: id ") and "used twice" in e for e in errs)


def test_reason_on_an_outcome_without_reasons(spec, api, ids):
    row = next(r for r in spec["closing"]["outcome_table"] if not r["reasons"])
    i = next(i for i, s in enumerate(spec["situations"]) if s["outcome"] == row["outcome"])
    spec["situations"][i]["reason"] = "anything"
    assert any(e.startswith(f"situations[{i}].reason: outcome {row['outcome']!r} takes no reason") for e in errors(spec, api, ids))


def test_identity_field_hint_and_pattern(spec, api, ids):
    spec["identity"]["questions"][0]["field"] = "shoe_size"
    spec["identity"]["questions"][1]["complete_pattern"] = "([0-9]"
    spec["identity"]["hints"].append({"value": "nonsense_hint", "question": "any", "say_template": "Again please.",
                                      "counts_as_wrong": False, "refs": []})
    errs = errors(spec, api, ids)
    assert any(e.startswith("identity.questions[0].field: 'shoe_size' is not one of the tool's values") for e in errs)
    assert any(e.startswith("identity.questions[1].complete_pattern: not a valid regular expression") for e in errs)
    assert any("'nonsense_hint' is not one of the tool's values" in e for e in errs)


def test_tool_name_is_not_a_code(spec, api, ids):
    spec["forbidden_codes"] = [spec["bindings"]["tools"]["close"]]
    assert any(e.startswith("forbidden_codes[0]:") and "is a tool name" in e for e in errors(spec, api, ids))


def test_product_kinds_need_names(spec, api, ids):
    gone = spec["disclosure"]["product_names"].pop()
    assert f"disclosure.product_names: product kind {gone['kind']!r} has no name" in errors(spec, api, ids)


def test_expression_and_callback_window(spec, api, ids):
    spec["third_party"]["callback"]["day_rule"] = "d.weekday() < 5"
    spec["third_party"]["callback"]["windows"] = [{"start": "18:00", "end": "09:00"}]
    errs = errors(spec, api, ids)
    assert any(e.startswith("third_party.callback.day_rule: Attribute is not allowed") for e in errs)
    assert "third_party.callback.windows[0]: start must be before end" in errs


def test_phrase_must_be_verbatim_and_used(spec, api, ids):
    spec["phrases"].append({"text": "Have a lovely day.", "used_in": ["confirm.question"], "refs": []})
    errs = errors(spec, api, ids)
    n = len(spec["phrases"]) - 1
    assert f"phrases[{n}].text: 'Have a lovely day.' is not verbatim in the brief" in errs
    assert f"phrases[{n}]: confirm.question does not contain 'Have a lovely day.' word for word" in errs


def test_rule_index_coverage_and_paths(spec, api, ids):
    dropped = spec["rule_index"].pop(0)
    spec["rule_index"].append({"rule": "R999", "paths": ["opening.nowhere", "residual:X999"]})
    errs = errors(spec, api, ids)
    assert any(e.startswith(f"rule_index: digest rule {dropped['rule']} is not indexed") for e in errs)
    assert "rule_index: R999 is not a digest rule" in errs
    assert any("no spec path 'opening.nowhere'" in e for e in errs) and any("no residual rule 'X999'" in e for e in errs)


def test_bindings_errors_are_prefixed(spec, api, ids):
    del spec["bindings"]["request"]["record_promise"]["date"]
    errs = errors(spec, api, ids)
    assert any(e.startswith("bindings.request.record_promise.date: required body field") for e in errs)


def test_schema_and_semantic_errors_together(spec, api, ids):
    spec["identity"]["limits"]["total_wrong"] = "three"
    spec["identity"]["on_failed"] = "nowhere"
    errs = errors(spec, api, ids)
    assert any(e.startswith("identity.limits.total_wrong: expected integer or 'not_specified'") for e in errs)
    assert "identity.on_failed: 'nowhere' is not a situation id" in errs


def test_facts_report_not_specified_and_residuals(spec):
    f = sp.facts(spec)
    assert all(sp.resolve(spec, p) == (True, sp.NS) for p in f["not_specified"])
    assert sum(f["residuals"].values()) == len(spec["residual_rules"])
    assert all(r["trigger"] for r in f["flow_residuals"])


# ---------- expressions evaluated on synthetic inputs (item 08) ----------

def test_seeded_expression_that_raises_on_one_synthetic_input(spec, api, ids):
    spec["negotiation"]["first_ask"]["amount"] = "due_amount / abs(days_overdue - 7)"  # passes the AST check
    errs = errors(spec, api, ids)
    hit = [e for e in errs if e.startswith("negotiation.first_ask.amount: raises ZeroDivisionError")]
    assert hit and "days_overdue=7" in hit[0] and "now=" in hit[0]


def test_expression_with_the_wrong_type(spec, api, ids):
    spec["negotiation"]["first_ask"]["date"] = "add_business_days(today, 2) if days_overdue > 30 else 'soon'"
    spec["third_party"]["callback"]["day_rule"] = "add_business_days(today, 1)"
    errs = errors(spec, api, ids)
    assert any(e.startswith("negotiation.first_ask.date: returns str 'soon', expected a date") for e in errs)
    assert any(e.startswith("third_party.callback.day_rule: returns date") and "expected true or false" in e
               for e in errs)


def test_helper_misuse_and_negative_amounts_are_caught(spec, api, ids):
    spec["negotiation"]["first_ask"]["date"] = "add_business_days(today, due_amount)"
    spec["negotiation"]["first_ask"]["amount"] = "due_amount - 100"
    errs = errors(spec, api, ids)
    assert any(e.startswith("negotiation.first_ask.date: raises TypeError") for e in errs)
    assert any(e.startswith("negotiation.first_ask.amount: returns -") and "expected an amount of 0 or more" in e
               for e in errs)


def test_expressions_that_hold_on_every_synthetic_day(spec, api, ids):
    spec["negotiation"]["first_ask"]["date"] = ("last_bd_of_week(today, 1) if today == last_bd_of_week(today, 0) "
                                                "else roll(today, 'next')")
    spec["negotiation"]["first_ask"]["amount"] = "round_half_up(max(due_amount * 0.3, minimum_payment), 2)"
    spec["third_party"]["callback"]["day_rule"] = "d >= today and d <= add_business_days(today, 2) and is_business_day(d)"
    assert errors(spec, api, ids) == []


def test_synthetic_nows_cover_weekends_holiday_eves_and_after_hours(spec):
    from builder import exprs
    nows = exprs.synthetic_nows(spec)
    days = {n.date() for n in nows}
    for h in spec["calendar"]["holidays"]:
        d = __import__("datetime").date.fromisoformat(h)
        assert d in days and d - __import__("datetime").timedelta(days=1) in days
    assert {n.weekday() for n in nows} == set(range(7))
    assert any(n.hour >= 21 for n in nows) and any(n.hour < 8 for n in nows)
