"""Spec building blocks that need no full spec: schema checks, paths and patches, expressions, the digest helpers."""
from builder import spec as sp
from builder import specgen as sg


def test_schema_reports_missing_and_not_specified_hint():
    errs = sp.schema_errors({"spec_version": "0"})
    assert "call_variables: missing (required)" in errs
    sub = {"type": "object", "required": ["n"], "properties": {"n": {"$ref": "#/$defs/count"}}}
    assert sp.schema_errors({}, sub) == ["n: missing (required, may be 'not_specified')"]


def test_schema_any_of_with_not_specified():
    count = {"$ref": "#/$defs/count"}
    assert sp.schema_errors(3, count) == [] and sp.schema_errors("not_specified", count) == []
    (e,) = sp.schema_errors("three", count)
    assert "expected integer or 'not_specified'" in e
    assert sp.schema_errors(True, {"$ref": "#/$defs/switch"}) == []


def test_schema_pattern_enum_extra_fields():
    s = {"type": "object", "additionalProperties": False, "properties": {"t": {"$ref": "#/$defs/hhmm"},
                                                                          "m": {"enum": ["a", "b"]}}}
    errs = sp.schema_errors({"t": "25:00", "m": "c", "x": 1}, s)
    assert any(e.startswith("t: '25:00' does not match") for e in errs)
    assert "m: 'c' is not one of 'a', 'b'" in errs and any(e.startswith("x: unknown field") for e in errs)


def test_resolve_by_index_and_id():
    spec = {"situations": [{"id": "a", "say": "x"}, {"id": "b", "say": "y"}],
            "identity": {"questions": [{"field": "q1"}]}}
    assert sp.resolve(spec, "situations[b].say") == (True, "y")
    assert sp.resolve(spec, "situations.a.say") == (True, "x")
    assert sp.resolve(spec, "identity.questions[0].field") == (True, "q1")
    assert sp.resolve(spec, "identity.questions[q1]") == (True, {"field": "q1"})
    assert sp.resolve(spec, "situations[c]")[0] is False


def test_apply_patches_set_remove_append_and_errors():
    spec = {"a": {"b": 1}, "l": [{"id": "x", "v": 1}]}
    out, errs = sp.apply_patches(spec, [
        {"op": "set", "path": "a.b", "value": 2},
        {"op": "set", "path": "a.c.d", "value": 3},
        {"op": "set", "path": "l[x].v", "value": 5},
        {"op": "append", "path": "l", "value": {"id": "y"}},
        {"op": "remove", "path": "a.nope.z"},
        {"op": "set", "path": "l[z]", "value": {"id": "z"}},
    ])
    assert out == {"a": {"b": 2, "c": {"d": 3}}, "l": [{"id": "x", "v": 5}, {"id": "y"}, {"id": "z"}]}
    assert len(errs) == 1 and "patch 4" in errs[0]
    assert spec == {"a": {"b": 1}, "l": [{"id": "x", "v": 1}]}  # the input is untouched


def test_expressions_whitelist():
    ok = "d >= today and is_business_day(d) and d <= add_business_days(today, 2)"
    assert sp.expr_errors(ok, {"d", "today"}) == []
    assert sp.expr_errors("max(due_amount * 0.2, 10) if kind == 'x' else due_amount", {"due_amount", "kind"}) == []
    assert any("unknown name 'x'" in e for e in sp.expr_errors("x + 1", {"d"}))
    assert any("Attribute" in e for e in sp.expr_errors("d.year", {"d"}))
    assert any("Lambda" in e or "not allowed" in e for e in sp.expr_errors("(lambda: 1)()", set()))
    assert sp.expr_errors("d >=", {"d"})[0].startswith("not a valid expression")


def test_occurs_ignores_case_markdown_and_placeholder_style():
    assert sp.occurs("Ask \"Hello {name}?\"", "**ask** \"hello {{name}}?\"")
    assert sp.occurs("the fee", "Say the fee. Then stop.")
    assert not sp.occurs("fee", "coffee")


def test_renumber_follows_references():
    d = {"rules": [{"id": "R10", "replaced_by": "R12"}, {"id": "R12", "replaced_by": ""}],
         "amendments": [{"target": "R10", "by": "R12"}]}
    d = sg.renumber(d)
    assert [r["id"] for r in d["rules"]] == ["R1", "R2"] and d["rules"][0]["replaced_by"] == "R2"
    assert d["amendments"][0] == {"target": "R1", "by": "R2"}


def test_statements_and_coverage():
    material = ("# Title of the thing\n\nThe library calls members. It is polite.\n\n| Days | Fee |\n|---|---|\n"
                "| 1 to 5 | 2.00 |\n\n1. Say \"Hello there, friend.\" first.\n2. Never shout at a member.\n")
    st = sg.statements(material)
    assert "The library calls members." in st and "| 1 to 5 | 2.00 |" in st and "Never shout at a member." in st
    digest = {"rules": [{"id": "R1", "quote": "Say \"Hello there, friend.\" first.", "status": "binding"},
                        {"id": "R2", "quote": "1 to 5 | 2.00", "status": "binding"},
                        {"id": "R3", "quote": "Never whisper.", "status": "binding"}],
              "skipped": [{"quote": "Title of the thing"}, {"quote": "The library calls members. It is polite."}]}
    c = sg.check_digest(digest, material)
    assert c["unverified_quotes"] == ["R3"] and c["quotes_verbatim"] == 2
    assert c["uncovered"] == ["Never shout at a member."]


def test_prompts_render_without_brief_values():
    text = sg.SPEC_PROMPT.format(templates=sg.templates_text(), expressions=sg.expressions_text(),
                                 binding_roles=sg.binding_roles_text(), schema=sg.schema_text(), tool_api="T",
                                 digest="D", material="M")
    assert "{{name}}" in text and "fully_verified" in text and "situations[some_id]" in text
    assert sg.REPAIR_PROMPT.format(errors="e", spec="s", context="c").count('{"patches"') == 1
