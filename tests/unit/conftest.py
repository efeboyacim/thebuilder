import pytest

from builder import bindings as bd
from builder import toolapi as ta
from tests.helpers import FIX, ROOT, load


@pytest.fixture
def api_a():
    return ta.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture
def api_r():
    return ta.parse(load(FIX / "openapi-renamed.json"))


@pytest.fixture
def b_a():
    return bd.Bindings.from_dict(load(FIX / "bindings-a.json"))


@pytest.fixture
def b_r():
    return bd.Bindings.from_dict(load(FIX / "bindings-renamed.json"))
