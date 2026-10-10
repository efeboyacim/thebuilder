import json, pathlib, shutil

import pytest

from builder import ingest
from builder.ingest import InputError

ROOT = pathlib.Path(__file__).resolve().parents[2]
BRIEF_A = str(ROOT / "briefs" / "brief-a.md")
BRIEF_B = str(ROOT / "briefs" / "brief-b.md")
API = str(ROOT / "tools" / "openapi.json")
CASES = str(ROOT / "public-test-set.json")


def write(path, data):
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return str(path)


@pytest.mark.parametrize("order", [(BRIEF_A, API), (API, BRIEF_A)])
def test_one_brief_and_api_in_any_order(order):
    inp = ingest.ingest(list(order))
    assert [b.path for b in inp.briefs] == [BRIEF_A]
    assert inp.tool_api.path == API and inp.tool_api.data["openapi"]
    assert inp.cases is None and inp.warnings == []


@pytest.mark.parametrize("order", [(API, BRIEF_A, BRIEF_B), (BRIEF_A, API, BRIEF_B), (BRIEF_A, BRIEF_B, API)])
def test_briefs_keep_their_order_wherever_the_api_is(order):
    inp = ingest.ingest(list(order))
    assert [b.path for b in inp.briefs] == [BRIEF_A, BRIEF_B]
    assert inp.tool_api.path == API


def test_brief_order_is_the_given_order_not_sorted():
    assert [b.path for b in ingest.ingest([BRIEF_B, BRIEF_A, API]).briefs] == [BRIEF_B, BRIEF_A]


def test_api_found_by_content_not_by_name(tmp_path):
    api = write(tmp_path / "bank-tools.txt", (ROOT / "tools" / "openapi.json").read_text(encoding="utf-8"))
    brief = write(tmp_path / "brief.json.md", "# A brief\n\nText.\n")
    inp = ingest.ingest([api, brief])
    assert inp.tool_api.path == api and [b.path for b in inp.briefs] == [brief]


def test_cases_file_is_classified():
    inp = ingest.ingest([BRIEF_A, CASES, API])
    assert inp.cases.path == CASES and len(inp.cases.data) == 13
    assert [i.kind for i in inp.all] == ["brief", "tool_api", "cases"]


def test_record_has_hash_and_size():
    rec = ingest.ingest([BRIEF_A, API]).briefs[0].record()
    assert rec["kind"] == "brief" and rec["bytes"] == pathlib.Path(BRIEF_A).stat().st_size
    assert len(rec["sha256"]) == 64 and rec["encoding"] == "utf-8"


@pytest.mark.parametrize("paths,msg", [
    ([BRIEF_A], "no tool API"),
    ([API], "no brief"),
    ([], "no input"),
    ([BRIEF_A, API, str(ROOT / "nope.md")], "no such file"),
    ([str(ROOT / "briefs"), API], "directory"),
])
def test_unusable_inputs(paths, msg):
    with pytest.raises(InputError, match=msg):
        ingest.ingest(paths)


def test_two_tool_apis_is_an_error(tmp_path):
    other = str(tmp_path / "copy.json")
    shutil.copy(API, other)
    with pytest.raises(InputError, match="more than one tool API"):
        ingest.ingest([BRIEF_A, API, other])


def test_broken_json_file_is_an_error(tmp_path):
    bad = write(tmp_path / "api.json", '{"openapi": "3.0.3", ')
    with pytest.raises(InputError, match="not valid JSON"):
        ingest.ingest([BRIEF_A, bad])


def test_json_name_with_text_content_is_an_error(tmp_path):
    bad = write(tmp_path / "api.json", "openapi: 3.0.3\n")
    with pytest.raises(InputError, match="not JSON"):
        ingest.ingest([BRIEF_A, bad])


def test_other_json_object_is_brief_material(tmp_path):
    codes = write(tmp_path / "codes.txt", json.dumps({"codes": {"X1": "something"}}))
    assert [b.path for b in ingest.ingest([codes, API]).briefs] == [codes]


def test_same_file_twice_is_read_once():
    inp = ingest.ingest([BRIEF_A, API, BRIEF_A])
    assert len(inp.briefs) == 1 and "given twice" in inp.warnings[0]


def test_empty_brief_is_an_error(tmp_path):
    with pytest.raises(InputError, match="empty"):
        ingest.ingest([write(tmp_path / "b.md", " \n\n"), API])


def test_utf8_bom_and_non_ascii(tmp_path):
    p = write(tmp_path / "b.md", "﻿# Müşteri notu\n\nÖdeme: 1.250,00 €\n".encode("utf-8"))
    b = ingest.ingest([p, API]).briefs[0]
    assert b.encoding == "utf-8" and b.text.startswith("# Müşteri") and b.sections == [(1, "Müşteri notu")]


def test_non_utf8_falls_back_with_a_warning(tmp_path):
    p = write(tmp_path / "b.md", "# Memo\n\nFee \x80 5 and caf\xe9\n".encode("latin-1"))
    inp = ingest.ingest([p, API])
    assert inp.briefs[0].encoding == "cp1252" and "€" in inp.briefs[0].text
    assert "not valid utf-8" in inp.warnings[0]


def test_sections_skip_code_fences():
    text = "# Mail\n\ntext\n```\n# not a heading\n```\n## Policy extract ##\n### Detail\n#nospace\n"
    assert ingest.sections(text) == [(1, "Mail"), (2, "Policy extract"), (3, "Detail")]


def test_default_slot_from_brief_names():
    one = ingest.ingest([BRIEF_A, API]).briefs
    two = ingest.ingest([API, BRIEF_A, BRIEF_B]).briefs
    assert ingest.default_slot(one) == "hub-brief-a"
    assert ingest.default_slot(two) == "hub-brief-a+brief-b"


def test_slot_precedence():
    briefs = ingest.ingest([BRIEF_A, API]).briefs
    assert ingest.resolve_slot("cli", {"BUILDER_SLOT": "env"}, briefs) == "cli"
    assert ingest.resolve_slot(None, {"BUILDER_SLOT": "env"}, briefs) == "env"
    assert ingest.resolve_slot(None, {}, briefs) == "hub-brief-a"


def test_slot_sanitized():
    assert ingest.sanitize_slot("my slot/../x") == "my-slot-x"
    assert len(ingest.sanitize_slot("a" * 200)) == ingest.SLOT_MAX
    with pytest.raises(InputError):
        ingest.sanitize_slot("../")


def test_tool_api_summary():
    s = ingest.tool_api_summary(ingest.ingest([BRIEF_A, API]).tool_api)
    assert s["operations"] == 7 and s["servers"] and s["openapi"].startswith("3.")
