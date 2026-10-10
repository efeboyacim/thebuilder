import pytest

from builder import llm
from builder.llm import LLM, LLMError, Reply
from builder.trace import Trace


class FakeBackend:
    name = "fake"

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(*results, **kw):
    t = Trace(echo=False)
    sleeps = []
    return LLM(trace=t, backend=FakeBackend(*results), sleep=sleeps.append, **kw), t, sleeps


def calls(t):
    return [e for e in t.events if e["ev"] == "model_call"]


def test_success_writes_one_model_call():
    m, t, _ = make(Reply(text="hello", tokens_in=10, tokens_out=2, model="claude-opus-5-5"), model="claude-opus-5-5")
    assert m.text("hi", purpose="digest: read the brief") == "hello"
    (e,) = calls(t)
    assert e["model"] == "claude-opus-5-5" and e["purpose"] == "digest: read the brief" and e["ok"] is True
    assert e["attempt"] == 1 and e["tokens_in"] == 10 and e["tokens_out"] == 2 and e["backend"] == "fake"


def test_retry_then_success_with_backoff():
    m, t, sleeps = make(LLMError("overloaded"), RuntimeError("socket closed"), Reply(text="ok"))
    assert m.text("hi", purpose="p") == "ok"
    assert [(e["attempt"], e["ok"]) for e in calls(t)] == [(1, False), (2, False), (3, True)]
    assert sleeps == list(llm.BACKOFF) and "RuntimeError" in calls(t)[1]["error"]


def test_non_retryable_failure_stops_at_once():
    m, t, sleeps = make(LLMError("refused", retryable=False), Reply(text="never"))
    with pytest.raises(LLMError, match="1 attempt"):
        m.text("hi", purpose="p")
    assert len(calls(t)) == 1 and sleeps == []


def test_gives_up_after_retries():
    m, t, _ = make(*[LLMError("down")] * 3)
    with pytest.raises(LLMError, match="3 attempt"):
        m.text("hi", purpose="p")
    assert len(calls(t)) == 1 + llm.RETRIES


def test_json_with_schema_uses_backend_data():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    m, _, _ = make(Reply(text='{"a": 1}', data={"a": 1}))
    assert m.json("x", purpose="p", schema=schema) == {"a": 1}
    assert m.backend.calls[0]["schema"] == schema and m.backend.calls[0]["effort"] == m.effort


def test_json_without_schema_parses_text():
    m, _, _ = make(Reply(text='Here it is:\n```json\n{"rules": [1, 2]}\n```\n'))
    assert m.json("x", purpose="p") == {"rules": [1, 2]}


@pytest.mark.parametrize("text,value", [
    ('{"a": 1}', {"a": 1}),
    ('  [1, 2]  ', [1, 2]),
    ('```\n{"a": "b"}\n```', {"a": "b"}),
    ('Sure. {"a": {"b": [1]}} Done.', {"a": {"b": [1]}}),
])
def test_parse_json(text, value):
    assert llm.parse_json(text) == value


def test_parse_json_failure_is_retryable():
    with pytest.raises(LLMError) as e:
        llm.parse_json("no json here")
    assert e.value.retryable


def test_model_and_effort_from_env(monkeypatch):
    monkeypatch.setenv("BUILDER_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("BUILDER_EFFORT", "medium")
    m = LLM(backend=FakeBackend())
    assert m.model == "claude-sonnet-5-5" and m.effort == "medium"
    monkeypatch.delenv("BUILDER_MODEL")
    monkeypatch.delenv("BUILDER_EFFORT")
    m = LLM(backend=FakeBackend())
    assert m.model == llm.DEFAULT_MODEL == "claude-opus-5-5" and m.effort == llm.DEFAULT_EFFORT


def test_backend_choice():
    assert llm.default_backend_name({"ANTHROPIC_API_KEY": "sk-ant-x"}) == "api"
    assert llm.default_backend_name({}) == "sdk"
    assert llm.default_backend_name({"ANTHROPIC_API_KEY": "k", "BUILDER_LLM_BACKEND": "sdk"}) == "sdk"
    with pytest.raises(ValueError):
        llm.make_backend("other")


def test_host_session_env_cleared_only_when_nested():
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "ANTHROPIC_BASE_URL": "u", "FREYA_API_KEY": "f",
           "PATH": "p"}
    assert llm.clear_host_session_env(env) == ["ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID"]
    assert env == {"FREYA_API_KEY": "f", "PATH": "p"}
    plain = {"ANTHROPIC_BASE_URL": "u"}
    assert llm.clear_host_session_env(plain) == [] and plain == {"ANTHROPIC_BASE_URL": "u"}


def test_cleared_env_is_traced_by_name():
    b = FakeBackend()
    b.cleared_env = ["CLAUDECODE"]
    t = Trace(echo=False)
    LLM(trace=t, backend=b)
    (w,) = [e for e in t.events if e["ev"] == "warning"]
    assert w["names"] == ["CLAUDECODE"]


def test_side_models_are_traced():
    m, t, _ = make(Reply(text="x", model="claude-opus-5-5", side_models=["claude-haiku-5-5"]))
    m.text("hi", purpose="p")
    (e,) = calls(t)
    assert e["model"] == "claude-opus-5-5" and e["side_models"] == ["claude-haiku-5-5"]
