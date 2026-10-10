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


def test_unfinished_stage_is_an_error_with_a_trace(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code, ev, _ = run(tmp_path, A, API)
    assert code == 1 and ev[-1]["status"] == "error" and "not implemented" in ev[-1]["error"]


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
