import io, json

from builder import trace as tr

FREYA = "fak_" + "Zx9" * 8
TEAM = "team-key-" + "q" * 20
ANTH = "sk-ant-api03-" + "Ab_9-" * 6


def secrets_env(monkeypatch):
    monkeypatch.setenv("FREYA_API_KEY", FREYA)
    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", TEAM)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTH)


def test_redact_env_values_and_patterns_everywhere(monkeypatch):
    secrets_env(monkeypatch)
    obj = {"argv": ["--key", FREYA], FREYA: "as key", "nested": {"h": f"Bearer {FREYA}", "team": TEAM},
           "other": "fak_SomethingElse1 and sk-ant-oat01-xyz_ABC-9", "n": 3, "ok": True, "none": None}
    out = tr.redact(obj, tr.secret_values())
    text = json.dumps(out)
    for s in (FREYA, TEAM, ANTH, "fak_SomethingElse1", "sk-ant-oat01"):
        assert s not in text
    assert out["n"] == 3 and out["ok"] is True and out["none"] is None
    assert out["nested"]["h"] == "Bearer [REDACTED]"


def test_short_env_values_are_not_secrets(monkeypatch):
    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", "abc")
    assert tr.redact("abc def", tr.secret_values()) == "abc def"


def test_trace_buffers_until_open_then_writes_lf_jsonl(tmp_path, monkeypatch):
    secrets_env(monkeypatch)
    out = io.StringIO()
    t = tr.Trace(stream=out)
    t.event("run_start", argv=["x", TEAM])
    path = t.open(str(tmp_path / "traces"), "slot-1")
    t.warning(f"header Authorization: Bearer {FREYA}")
    t.close("ok", outputs={"trace": path})
    raw = open(path, "rb").read()
    assert b"\r\n" not in raw
    lines = [json.loads(l) for l in raw.decode("utf-8").splitlines()]
    assert [l["ev"] for l in lines] == ["run_start", "warning", "run_end"]
    assert lines[-1]["status"] == "ok" and "seconds" in lines[-1]
    assert all(l["t"].endswith("Z") for l in lines)
    for s in (FREYA, TEAM):
        assert s not in raw.decode("utf-8") and s not in out.getvalue()
    assert out.getvalue().count("\n") == 3
    assert path.endswith(".jsonl") and "slot-1-" in path


def test_secret_captured_at_start_survives_env_clearing(tmp_path, monkeypatch):
    secrets_env(monkeypatch)
    t = tr.Trace(echo=False)
    t.open(str(tmp_path), "s")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    rec = t.event("model_call", purpose=f"oops {ANTH[:-1]}x")  # still matches the pattern
    rec2 = t.event("warning", text="value " + TEAM)
    assert ANTH[:20] not in json.dumps(rec) and TEAM not in json.dumps(rec2)


def test_explicit_event_time_and_counts(tmp_path):
    t = tr.Trace(echo=False)
    rec = t.event("run_start", t=0)
    assert rec["t"] == "1970-01-01T00:00:00Z"
    t.event("model_call", model="m", purpose="p")
    t.event("model_call", model="m", purpose="q")
    assert t.count("model_call") == 2 and t.count("self_test") == 0
