"""Shared test helpers. Tests may hold Brief A/B data; the builder never imports this."""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def mock_ledger(stub):
    """A stub bank ledger in the practice bank's ledger format, for the kit's tools/score.py."""
    led = stub.ledger
    return {"outcome": led["outcome"], "reason": led["reason"] or None,
            "promises": [{"product_id": p, "amount": a, "date": d} for p, a, d in led["promises"]],
            "notes": [{"product_id": p, "reason": r} for p, r in led["notes"]],
            "callbacks": [{"date": d, "time": t} for d, t in led["callbacks"]],
            "transfers": [{"queue": q} for q in led["transfers"]],
            "calls": [{"tool": c["tool"], "result": c.get("response") or {}} for c in led["calls"]]}


def kit_score():
    """The kit's ledger scorer (tools/score.py) as a module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kit_score", ROOT / "tools" / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
