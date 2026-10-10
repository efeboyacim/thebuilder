"""The spec stage end to end with a fake model: digest, spec, validation, repair, artifacts, trace."""
import copy, json

import pytest

from builder import ingest, specgen, toolapi
from builder import spec as sp
from builder.llm import LLM, Reply
from builder.trace import Trace
from tests.helpers import FIX, ROOT, load

A, API = str(ROOT / "briefs" / "brief-a.md"), str(ROOT / "tools" / "openapi.json")


class FakeBackend:
    name = "fake"

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, **kw):
        self.prompts.append(kw["prompt"])
        return self.replies.pop(0)


@pytest.fixture
def spec_a():
    return load(FIX / "spec-a-v0.json")


@pytest.fixture
def digest_a():
    return load(FIX / "digest-a-v0.json")


def _ctx(*replies):
    inputs = ingest.ingest([A, API])
    trace = Trace(echo=False)
    backend = FakeBackend(*replies)
    llm = LLM(trace=trace, backend=backend, sleep=lambda s: None)
    api = toolapi.parse(inputs.tool_api.data)
    return {"trace": trace, "api": api, "inputs": inputs, "llm": llm}, backend


def test_fixture_spec_is_valid(spec_a, digest_a):
    inputs = ingest.ingest([A, API])
    api = toolapi.parse(inputs.tool_api.data)
    r = sp.validate(spec_a, api, inputs.briefs[0].text, specgen.binding_rule_ids(digest_a))
    assert r.errors == [] and r.warnings == []
    assert r.info["rule_coverage"]["indexed"] == r.info["rule_coverage"]["rules"]


def test_run_valid_first_time(tmp_path, spec_a, digest_a):
    ctx, backend = _ctx(Reply(text="", data=copy.deepcopy(digest_a)), Reply(text=json.dumps(spec_a)))
    spec, report, summary = specgen.run(ctx, str(tmp_path))
    assert report.ok and summary["repair_rounds"] == 0 and spec == spec_a
    assert {p.name for p in tmp_path.iterdir()} == {"digest.json", "digest.md", "spec.json", "spec_checks.json"}
    assert "verbatim" in (tmp_path / "digest.md").read_text(encoding="utf-8")
    purposes = [e["purpose"] for e in ctx["trace"].events if e["ev"] == "model_call"]
    assert purposes[0].startswith("digest:") and purposes[1].startswith("spec:") and len(purposes) == 2
    assert "R1" in backend.prompts[1] and "list_loans" in backend.prompts[1]  # digest and generic example


def test_run_repairs_with_patches(tmp_path, spec_a, digest_a):
    broken = copy.deepcopy(spec_a)
    good_field = broken["identity"]["questions"][0]["field"]
    broken["identity"]["questions"][0]["field"] = "mother_maiden_name"
    broken["rule_index"] = broken["rule_index"][1:]
    first = spec_a["rule_index"][0]
    patches = {"patches": [{"op": "set", "path": "identity.questions[0].field", "value": good_field},
                           {"op": "append", "path": "rule_index", "value": first}]}
    ctx, backend = _ctx(Reply(text="", data=copy.deepcopy(digest_a)), Reply(text=json.dumps(broken)),
                        Reply(text=json.dumps(patches)))
    spec, report, summary = specgen.run(ctx, str(tmp_path))
    assert report.ok and summary["repair_rounds"] == 1
    assert "mother_maiden_name" in backend.prompts[2] and f"digest rule {first['rule']} is not indexed" in backend.prompts[2]
    checks = [e for e in ctx["trace"].events if e["ev"] == "check" and e["name"].startswith("spec_validate")]
    assert [c["ok"] for c in checks] == [False, True]


def test_run_gives_up_after_two_rounds(tmp_path, spec_a, digest_a):
    broken = copy.deepcopy(spec_a)
    broken["situations"] = broken["situations"][1:]
    ctx, _ = _ctx(Reply(text="", data=copy.deepcopy(digest_a)), Reply(text=json.dumps(broken)),
                  Reply(text='{"patches": []}'), Reply(text='{"patches": []}'))
    with pytest.raises(specgen.SpecGenError, match="after 2 repair round"):
        specgen.run(ctx, str(tmp_path))
    assert json.loads((tmp_path / "spec_checks.json").read_text(encoding="utf-8"))["errors"]
