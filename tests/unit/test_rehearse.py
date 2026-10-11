"""tools/rehearse.py, offline: case files merge over their PUB base case (item 15)."""
import importlib.util, json

from tests.helpers import ROOT


def rehearse():
    spec = importlib.util.spec_from_file_location("rehearse", ROOT / "tools" / "rehearse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_case_takes_identity_and_names_from_its_base(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"id": "R-1", "base": "PUB-01", "variables": {"now": "2026-10-16T11:00"},
                                 "script": "s", "checks": ["c"], "expected": {"outcome": "promise"}}]),
                    encoding="utf-8")
    (case,) = rehearse().load_cases(path)
    assert case["identity"] == {"date_of_birth": "1988-03-14", "phone_last4": "4471"}
    assert case["variables"] == {"first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-16T11:00"}
    assert (case["brief"], case["family"]) == ("A", "Happy path") and case["script"] == "s"


def test_the_friday_case_file_loads():
    cases = rehearse().load_cases(ROOT / "tests" / "cases" / "pub01-friday.json")
    assert [c["id"] for c in cases] == ["R-PUB01-FRI"] and cases[0]["variables"]["now"].startswith("2026-10-16")
