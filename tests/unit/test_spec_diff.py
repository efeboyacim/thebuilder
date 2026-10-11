"""tools/spec_diff.py (item 20): facts, not wording. A missed memo shows up; reworded templates do not."""
import copy, importlib.util

import pytest

from builder import toolapi
from tests.helpers import FIX, ROOT, load


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("spec_diff", ROOT / "tools" / "spec_diff.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    return load(FIX / "spec-ab-v1.json")


def test_wording_is_not_a_difference(mod, api, spec):
    s = copy.deepcopy(spec)
    s["opening"]["template"] = "Good morning. " + s["opening"]["template"]
    s["situations"][0]["farewell"] = "Bye now."
    s["products"]["bands"][1]["deadline"] = "add_business_days(today, 3 + 4)"  # same values, other text
    assert mod.diff(spec, s, api) == []


@pytest.mark.parametrize("path,value,key", [
    ("calendar.holidays", ["2026-10-26"], "calendar.holidays"),
    ("products.bands[1].deadline", "add_business_days(today, 10)", "products.bands"),
    ("products.bands[0].attempts", 2, "products.bands"),
    ("third_party.callback.windows", [{"start": "10:00", "end": "17:00"}], "callback.windows"),
    ("failure_policy.record_promise.retries", 2, "failure.record_promise"),
])
def test_a_missed_memo_fact_is_a_difference(mod, api, spec, path, value, key):
    from builder import spec as sp
    s, errs = sp.apply_patches(spec, [{"op": "set", "path": path, "value": value}])
    assert not errs
    assert any(line.startswith(key) for line in mod.diff(spec, s, api))
