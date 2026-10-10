import glob, json, os, pathlib, subprocess, sys

from builder import build

ROOT = pathlib.Path(__file__).resolve().parents[2]
A, B, API = "briefs/brief-a.md", "briefs/brief-b.md", "tools/openapi.json"


def run(tmp_path, *argv, environ=None):
    code = build.main(list(argv), trace_dir=str(tmp_path), environ=environ or {})
    (path,) = glob.glob(str(tmp_path / "*.jsonl"))
    events = [json.loads(l) for l in open(path, encoding="utf-8")]
    return code, events, os.path.basename(path)


def test_until_ingest_one_brief(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    code, ev, name = run(tmp_path, A, API, "--until", "ingest")
    assert code == 0 and name.startswith("hub-brief-a-")
    assert ev[0]["ev"] == "run_start" and ev[-1]["ev"] == "run_end"
    assert [(i["path"], i["kind"]) for i in ev[0]["inputs"]] == [(A, "brief"), (API, "tool_api")]
    assert ev[-1]["status"] == "stopped" and ev[-1]["stopped_after"] == "ingest"
    out = capsys.readouterr().out
    assert "== build summary ==" in out and "brief 1      briefs/brief-a.md" in out and "tool API     tools/openapi.json" in out


def test_until_ingest_b_on_a_with_api_first(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    code, ev, name = run(tmp_path, API, A, B, "--until", "ingest")
    assert code == 0 and name.startswith("hub-brief-a+brief-b-")
    briefs = [i["path"] for i in ev[0]["inputs"] if i["kind"] == "brief"]
    assert briefs == [A, B] and [i["kind"] for i in ev[0]["inputs"]].count("tool_api") == 1
    (stage,) = [e for e in ev if e["ev"] == "stage"]
    assert [b["path"] for b in stage["summary"]["briefs"]] == [A, B]
    assert stage["summary"]["tool_api"]["operations"] == 7


def test_slot_option_and_env(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    _, ev, name = run(tmp_path, A, API, "--until", "ingest", "--slot", "m1 test", environ={"BUILDER_SLOT": "env"})
    assert name.startswith("m1-test-") and ev[0]["slot"] == "m1-test"
    (tmp_path / "2").mkdir()
    _, ev, name = run(tmp_path / "2", A, API, "--until", "ingest", environ={"BUILDER_SLOT": "env"})
    assert name.startswith("env-")


SPEC = "tests/fixtures/spec-a-v0.json"


def test_until_compile_writes_the_workflow(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code, ev, _ = run(tmp_path, A, API, "--until", "compile", "--spec", SPEC,
                      environ={"BUILDER_OUT_DIR": str(tmp_path / "out")})
    assert code == 0 and ev[-1]["stopped_after"] == "compile"
    wf = json.loads(next((tmp_path / "out").glob("*/workflow.json")).read_text(encoding="utf-8"))
    assert wf["initial_node"] == "init" and "brain" in wf["nodes"]
    assert {e["name"]: e["result"] for e in ev if e["ev"] == "self_test"} == {"graph_lint": "pass", "brain_code": "pass"}


def test_freya_stage_without_a_key_is_an_error_with_a_trace(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("FREYA_API_KEY", raising=False)
    code, ev, _ = run(tmp_path, A, API, "--spec", SPEC, environ={"BUILDER_OUT_DIR": str(tmp_path / "out")})
    assert code == 1 and ev[-1]["status"] == "error" and "FREYA_API_KEY is not set" in ev[-1]["error"]


def test_given_spec_until_spec(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code, ev, _ = run(tmp_path, A, API, "--until", "spec", "--spec", SPEC,
                      environ={"BUILDER_OUT_DIR": str(tmp_path / "out")})
    assert code == 0 and ev[-1]["stopped_after"] == "spec"
    (check,) = [e for e in ev if e["ev"] == "check"]
    (spec,) = [e for e in ev if e["ev"] == "spec"]
    assert check["name"] == "spec_validate_given" and check["ok"] and spec["given"] == SPEC
    assert not [e for e in ev if e["ev"] == "model_call"]
    ns = [e["text"] for e in ev if e["ev"] == "warning" and e["text"].startswith("not_specified:")]
    assert len(ns) == len(spec["not_specified"]) > 0


def test_invalid_given_spec_fails_the_spec_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    bad = json.loads((ROOT / SPEC).read_text(encoding="utf-8"))
    bad["situations"][0]["outcome"] = "maybe"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    (tmp_path / "t").mkdir()
    code, ev, _ = run(tmp_path / "t", A, API, "--until", "spec", "--spec", str(path))
    (stage,) = [e for e in ev if e["ev"] == "stage" and e["name"] == "spec"]
    assert code == 1 and stage["ok"] is False and "'maybe' is not one of the tool's values" in json.dumps(ev)
    assert ev[-1]["error"].startswith("SpecError") and not [e for e in ev if e["ev"] == "error"]


def test_bad_input_still_writes_a_trace(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code, ev, name = run(tmp_path, "briefs", API, "--until", "ingest")
    assert code == 1 and name.startswith(build.FALLBACK_SLOT + "-")
    assert ev[0]["ev"] == "run_start" and ev[0]["inputs"] == []
    assert ev[1]["ev"] == "stage" and ev[1]["ok"] is False and "directory" in ev[1]["error"]
    assert ev[-1]["ev"] == "run_end" and ev[-1]["status"] == "error"


def test_missing_spec_fixture_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code, ev, _ = run(tmp_path, A, API, "--until", "ingest", "--spec", "tests/fixtures/none.json")
    assert code == 1 and "--spec" in ev[-1]["error"]


def test_script_entry_point(tmp_path):
    """Run as the form runs it: a script, so builder/trace.py must not shadow the stdlib and imports must resolve."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env["BUILDER_TRACE_DIR"] = str(tmp_path)
    p = subprocess.run([sys.executable, "builder/build.py", API, A, "--until", "ingest"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert p.returncode == 0, p.stderr
    assert '"ev": "run_start"' in p.stdout and "stopped after ingest" in p.stdout
    (path,) = glob.glob(str(tmp_path / "hub-brief-a-*.jsonl"))
    events = [json.loads(l) for l in open(path, encoding="utf-8")]
    assert events[0]["argv"][1:] == [API, A, "--until", "ingest"] and events[-1]["status"] == "stopped"


def test_until_toolapi(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code, ev, _ = run(tmp_path, A, API, "--until", "toolapi")
    (stage,) = [e for e in ev if e["ev"] == "stage" and e["name"] == "toolapi"]
    assert code == 0 and stage["ok"] and len(stage["summary"]["tools"]) == 7
    assert ev[-1]["stopped_after"] == "toolapi"


def test_unusable_tool_api_fails_the_toolapi_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    bad = tmp_path / "api.json"
    bad.write_text('{"openapi": "3.0.3", "paths": {}}', encoding="utf-8")
    (tmp_path / "t").mkdir()
    code, ev, _ = run(tmp_path / "t", A, str(bad), "--until", "toolapi")
    (stage,) = [e for e in ev if e["ev"] == "stage" and e["name"] == "toolapi"]
    assert code == 1 and stage["ok"] is False and "no operations" in stage["error"]
    assert ev[-1]["status"] == "error" and ev[-1]["error"].startswith("ToolAPIError")
