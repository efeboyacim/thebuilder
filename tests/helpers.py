"""Shared test helpers. Tests may hold Brief A/B data; the builder never imports this."""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
