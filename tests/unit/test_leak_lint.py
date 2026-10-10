"""tools/leak_lint.py on a synthetic repository: brief literals in builder/ are found, tests/ is not scanned."""
import importlib.util, json

from tests.helpers import ROOT

spec = importlib.util.spec_from_file_location("leak_lint", ROOT / "tools" / "leak_lint.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)

BRIEF = """# Brief: Oak Library, overdue books

Oak Library calls members. Oak Library is fictional.

1. Say "Your book is overdue, dear reader." first.
2. Holidays: 2031-05-17. Call between 08:45 and 16:30. Fee code `late_fee_v2`, card ending 7731, fine 12.50.
"""
API = {"openapi": "3.0.3", "servers": [{"url": "https://oak-mock.example.org/v1"}],
       "paths": {"/renew_loan": {"post": {"operationId": "renew_loan", "requestBody": {"content": {"application/json": {
           "schema": {"type": "object", "properties": {"member_ref": {"type": "string"},
                                                       "medium": {"type": "string", "enum": ["audio_book", "dvd"]}}}}}}}}}}


def make_repo(tmp_path, code):
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "x.md").write_text(BRIEF, encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "openapi.json").write_text(json.dumps(API), encoding="utf-8")
    (tmp_path / "builder").mkdir()
    (tmp_path / "builder" / "a.py").write_text(code, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("x = 'late_fee_v2'\n", encoding="utf-8")
    return tmp_path


def kinds(findings):
    return sorted({(k, lit) for _, _, k, lit, _, _ in findings})


def test_finds_each_kind(tmp_path):
    code = ("A = '2031-05-17'\nB = '08:45'\nC = 'late_fee_v2'\nD = 7731\nE = 12.5\n"
            "F = 'your book is overdue, dear reader.'\nG = 'Oak Library'\nH = 'renew_loan'\nI = 'member_ref'\n"
            "J = 'audio_book'\nK = 'https://oak-mock.example.org'\n")
    root = make_repo(tmp_path, code)
    found = kinds(lint.scan(root, lint.candidates(root)))
    assert found == sorted([("date", "2031-05-17"), ("time", "08:45"), ("identifier", "late_fee_v2"),
                            ("number", "7731"), ("number", "12.50"), ("sentence", "Your book is overdue, dear reader."),
                            ("name", "Oak Library"), ("identifier", "renew_loan"), ("identifier", "member_ref"),
                            ("identifier", "audio_book"), ("host", "oak-mock.example.org")])


def test_clean_code_and_plain_words(tmp_path):
    root = make_repo(tmp_path, "# a medium, a fee, a library; dvd is a plain word\nTIMEOUT = 60\nx = 'grace oak'\n")
    assert lint.scan(root, lint.candidates(root)) == []
    assert lint.main(["--root", str(root)]) == 0


def test_main_exit_code_on_a_leak(tmp_path, capsys):
    root = make_repo(tmp_path, "C = 'late_fee_v2'\n")
    assert lint.main(["--root", str(root)]) == 1
    assert "builder/a.py:1: identifier 'late_fee_v2'" in capsys.readouterr().out
