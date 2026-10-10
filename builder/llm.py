"""The builder's model calls: one adapter, two back ends.

- `api`: the Anthropic Messages API (streaming; JSON through structured outputs), when ANTHROPIC_API_KEY is set.
- `sdk`: otherwise `claude_agent_sdk.query` with no tools, which uses the Claude login.

BUILDER_LLM_BACKEND=api|sdk forces a back end, BUILDER_MODEL the model (default claude-opus-5-5), BUILDER_EFFORT the
effort (default high). Every attempt writes a `model_call` trace event: model, one-line purpose, seconds, tokens, ok.
A failed attempt is retried RETRIES times with backoff, unless the failure cannot change on a retry.
"""
import asyncio, dataclasses, json, os, re, tempfile, time

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_SYSTEM = "You are a careful analyst. Follow the instructions exactly and answer only with what is asked."
RETRIES = 2
BACKOFF = (5, 20)  # seconds before retry 1 and 2
MAX_TOKENS = 32000
TIMEOUT = 900  # seconds per attempt
SDK_SYSTEM_ARG_MAX = 4000  # longer system prompts go through a file: Windows caps the command line


class LLMError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


@dataclasses.dataclass
class Reply:
    text: str
    data: object = None
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""  # the model that wrote the reply
    side_models: list = dataclasses.field(default_factory=list)  # other models the back end used on the way


def parse_json(text):
    """Parse a JSON value from model text: a bare value, or the first fenced or bracketed block."""
    s = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", s, re.S)
    candidates = [s] + ([m.group(1)] if m else [])
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = s.find(open_), s.rfind(close)
        if 0 <= i < j:
            candidates.append(s[i:j + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise LLMError("reply is not JSON: " + s[:200])


class ApiBackend:
    name = "api"

    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()

    def complete(self, *, model, prompt, system, schema, max_tokens, effort, timeout):
        a = self.anthropic
        output_config = {"effort": effort} if effort else {}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kw = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}],
              "system": system or DEFAULT_SYSTEM}
        if output_config:
            kw["output_config"] = output_config
        try:
            with self.client.with_options(timeout=timeout).messages.stream(**kw) as stream:
                msg = stream.get_final_message()
        except (a.BadRequestError, a.AuthenticationError, a.PermissionDeniedError, a.NotFoundError) as e:
            raise LLMError(f"{type(e).__name__}: {e}", retryable=False) from None
        if msg.stop_reason == "refusal":
            raise LLMError("the model declined the request", retryable=False)
        if msg.stop_reason == "max_tokens":
            raise LLMError(f"the reply hit max_tokens={max_tokens}", retryable=False)
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        tokens_in = sum(getattr(u, k, 0) or 0 for k in
                        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        data = parse_json(text) if schema else None
        return Reply(text=text, data=data, tokens_in=tokens_in, tokens_out=u.output_tokens or 0, model=msg.model)


def clear_host_session_env(environ=None):
    """Inside a Claude Code session the host's CLAUDE*/ANTHROPIC* variables break the Agent SDK's own CLI.
    Remove them from this process (the builder needs none of them). Returns the removed names."""
    environ = os.environ if environ is None else environ
    if not environ.get("CLAUDECODE"):
        return []
    names = sorted(k for k in environ if k.upper().startswith(("CLAUDE", "ANTHROPIC")))
    for k in names:
        environ.pop(k, None)
    return names


class SdkBackend:
    name = "sdk"

    def __init__(self):
        import claude_agent_sdk
        self.sdk = claude_agent_sdk
        self.cleared_env = clear_host_session_env()
        self.cwd = tempfile.mkdtemp(prefix="builder-llm-")  # no CLAUDE.md or project settings to pick up

    def complete(self, *, model, prompt, system, schema, max_tokens, effort, timeout):
        try:
            return asyncio.run(asyncio.wait_for(self._run(model, prompt, system, schema, effort), timeout))
        except asyncio.TimeoutError:
            raise LLMError(f"no reply within {timeout} s") from None

    async def _run(self, model, prompt, system, schema, effort):
        sdk = self.sdk
        system = system or DEFAULT_SYSTEM
        if len(system) > SDK_SYSTEM_ARG_MAX:
            path = os.path.join(self.cwd, f"system-{time.time_ns()}.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(system)
            system = {"type": "file", "path": path}
        opts = sdk.ClaudeAgentOptions(
            model=model, system_prompt=system, tools=[], allowed_tools=[], setting_sources=[], cwd=self.cwd,
            max_turns=4 if schema else 1, effort=effort,
            output_format={"type": "json_schema", "schema": schema} if schema else None)
        parts, result, used = [], None, None
        async for m in sdk.query(prompt=prompt, options=opts):
            if isinstance(m, sdk.AssistantMessage):
                parts += [b.text for b in m.content if isinstance(b, sdk.TextBlock)]
                used = m.model or used
            elif isinstance(m, sdk.ResultMessage):
                result = m
        if result is None:
            raise LLMError("the Agent SDK returned no result")
        if result.is_error:
            raise LLMError(f"Agent SDK error {result.subtype}: {result.errors or result.result}")
        text = result.result if isinstance(result.result, str) and result.result else "\n".join(parts)
        data = None
        if schema:
            data = result.structured_output if result.structured_output is not None else parse_json(text)
        u = result.usage or {}
        tokens_in = sum(u.get(k) or 0 for k in
                        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        used = used or model
        side = sorted(k for k in (result.model_usage or {}) if k != used)  # the CLI's own helper calls
        return Reply(text=text, data=data, tokens_in=tokens_in, tokens_out=u.get("output_tokens") or 0, model=used,
                     side_models=side)


def make_backend(name):
    if name == "api":
        return ApiBackend()
    if name == "sdk":
        return SdkBackend()
    raise ValueError(f"unknown LLM back end {name!r} (api or sdk)")


def default_backend_name(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("BUILDER_LLM_BACKEND") or ("api" if environ.get("ANTHROPIC_API_KEY") else "sdk")


class LLM:
    def __init__(self, trace=None, model=None, effort=None, backend=None, sleep=time.sleep):
        self.trace = trace
        self.model = model or os.environ.get("BUILDER_MODEL") or DEFAULT_MODEL
        self.effort = effort or os.environ.get("BUILDER_EFFORT") or DEFAULT_EFFORT
        self.backend = backend if backend is not None and not isinstance(backend, str) else \
            make_backend(backend or default_backend_name())
        self.sleep = sleep
        cleared = getattr(self.backend, "cleared_env", None)
        if cleared and trace:
            trace.warning("nested in a Claude Code session: host variables removed for the Agent SDK",
                          names=cleared)

    def text(self, prompt, *, purpose, system=None, max_tokens=MAX_TOKENS, timeout=TIMEOUT):
        return self.call(prompt, purpose=purpose, system=system, max_tokens=max_tokens, timeout=timeout).text

    def json(self, prompt, *, purpose, schema=None, system=None, max_tokens=MAX_TOKENS, timeout=TIMEOUT):
        """A JSON value. With a schema the back end constrains the output; without one the reply text is parsed."""
        r = self.call(prompt, purpose=purpose, system=system, schema=schema, max_tokens=max_tokens, timeout=timeout)
        return r.data if schema else parse_json(r.text)

    def call(self, prompt, *, purpose, system=None, schema=None, max_tokens=MAX_TOKENS, timeout=TIMEOUT):
        last = None
        for attempt in range(1, RETRIES + 2):
            t0 = time.time()
            try:
                r = self.backend.complete(model=self.model, prompt=prompt, system=system, schema=schema,
                                          max_tokens=max_tokens, effort=self.effort, timeout=timeout)
            except Exception as e:  # noqa: BLE001 - every failure is traced, then retried or raised
                last = e if isinstance(e, LLMError) else LLMError(f"{type(e).__name__}: {e}")
                self._event(purpose, t0, attempt, ok=False, error=str(last)[:300])
                if not last.retryable or attempt > RETRIES:
                    break
                self.sleep(BACKOFF[min(attempt, len(BACKOFF)) - 1])
                continue
            extra = {"side_models": r.side_models} if r.side_models else {}
            self._event(purpose, t0, attempt, ok=True, model=r.model or self.model, tokens_in=r.tokens_in,
                        tokens_out=r.tokens_out, **extra)
            return r
        raise LLMError(f"{purpose}: failed after {attempt} attempt(s): {last}", retryable=False)

    def _event(self, purpose, t0, attempt, ok, model=None, **fields):
        if self.trace:
            self.trace.event("model_call", model=model or self.model, purpose=purpose, backend=self.backend.name,
                             seconds=round(time.time() - t0, 1), ok=ok, attempt=attempt, **fields)
