"""Plan Z, blind: the kit's agentic builder (Claude Agent SDK + one skill) in a clean working copy.

    python3 planz/build.py <brief_path> [<brief_path> ...] <tool_api_path>

The tool API file is found by its content (JSON with an `openapi` key); the other files are briefs, in the order given.
The run happens in a fresh temp directory holding only what the skill needs: the kit docs (workflow format, tools and
code, API), freya.py, examples/base-agent.json, the briefs, the tool API as tools/openapi.json, a chat self-test tool
(tools/chat.py) and a blind copy of the skill (planz/SKILL.md). No CLAUDE.md, plan, results, earlier outputs, traces or
practice cases, and no way to run simulations from the skill: its only behavioural test is turn-by-turn chat with a
made-up customer id, so nothing reaches the bank. Slot: `<first brief stem>-z`. The export goes to
submission/<slot>.workflow.json and the trace to traces/planz-<slot>-<time>.jsonl.
"""
import asyncio, dataclasses, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
COPY = ["docs/workflow-format.md", "docs/tools-and-code.md", "docs/api.md", "freya.py", "examples/base-agent.json"]
STATE = ".freya-agents.json"
SECRET = re.compile(r"(fak_|sk-ant-)[A-Za-z0-9_\-]+")


def classify(paths):
    briefs, api = [], None
    for p in map(pathlib.Path, paths):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict) and "openapi" in data:
            if api is not None:
                raise SystemExit("two tool API files given")
            api = p
        else:
            briefs.append(p)
    if api is None or not briefs:
        raise SystemExit(__doc__)
    return briefs, api


def workdir(briefs, api):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="planz-"))
    for rel in COPY:
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(KIT / rel, tmp / rel)
    (tmp / "briefs").mkdir()
    names = []
    for n, b in enumerate(briefs, 1):
        name = f"{n}-{b.name}"
        shutil.copy(b, tmp / "briefs" / name)
        names.append("briefs/" + name)
    (tmp / "tools").mkdir()
    shutil.copy(api, tmp / "tools" / "openapi.json")
    shutil.copy(KIT / "planz" / "chat.py", tmp / "tools" / "chat.py")
    skill = tmp / ".claude" / "skills" / "build-phone-agent"
    skill.mkdir(parents=True)
    shutil.copy(KIT / "planz" / "SKILL.md", skill / "SKILL.md")
    if (KIT / STATE).exists():  # keeps the slot's workflow id, so a rerun updates the same workflow
        shutil.copy(KIT / STATE, tmp / STATE)
    return tmp, names


def python3_shim(tmp):
    """A python3 command for the skill when this machine has none (the kit's commands say python3)."""
    try:
        ok = subprocess.run(["python3", "--version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if ok:
        return None
    bin_dir = tmp / ".bin"
    bin_dir.mkdir()
    exe = pathlib.Path(sys.executable).as_posix()
    (bin_dir / "python3").write_text(f'#!/bin/sh\nexec "{exe}" "$@"\n', encoding="utf-8", newline="\n")
    (bin_dir / "python3.bat").write_text(f'@"{sys.executable}" %*\r\n', encoding="utf-8")
    os.chmod(bin_dir / "python3", 0o755)
    return bin_dir


class Trace:
    def __init__(self, path):
        self.path = path
        self.f = path.open("w", encoding="utf-8")

    def event(self, ev, **kw):
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ev": ev, **kw}, default=str)
        self.f.write(SECRET.sub("[redacted]", line) + "\n")
        self.f.flush()


async def run_skill(tmp, names, slot, model, trace, env):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query
    options = ClaudeAgentOptions(
        cwd=str(tmp), model=model, skills=["build-phone-agent"], setting_sources=["project"],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"], permission_mode="dontAsk", max_turns=150,
        env=env)
    prompt = (f"Use the build-phone-agent skill to build a phone agent for: {' '.join(names)} with the tool API "
              f"tools/openapi.json. Your slot is {slot}.")
    result, commands = None, []
    async for m in query(prompt=prompt, options=options):
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, TextBlock) and b.text.strip():
                    trace.event("model_call", model=m.model or model, purpose="builder: " + b.text.strip()[:140])
                    print(b.text, flush=True)
                elif isinstance(b, ToolUseBlock):
                    cmd = str(b.input.get("command") or b.input.get("file_path") or b.input)[:300]
                    commands.append(cmd)
                    trace.event("tool_use", model=m.model or model, tool=b.name, input=cmd)
                    print(f"  [{b.name}] {cmd[:120]}", flush=True)
        elif isinstance(m, ResultMessage):
            result = m
    return result, commands


def main():
    t0 = time.time()
    briefs, api = classify(sys.argv[1:])
    slot = briefs[0].stem + "-z"
    model = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
    (KIT / "traces").mkdir(exist_ok=True)
    trace = Trace(KIT / "traces" / f"planz-{slot}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    trace.event("run_start", builder="planz", briefs=[str(b) for b in briefs], tool_api=str(api), slot=slot, model=model)
    tmp, names = workdir(briefs, api)
    trace.event("workdir", path=str(tmp), files=sorted(p.relative_to(tmp).as_posix() for p in tmp.rglob("*") if p.is_file()))
    env = {"BASH_DEFAULT_TIMEOUT_MS": "300000", "BASH_MAX_TIMEOUT_MS": "600000",
           "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", "PYTHONUTF8": "1"}
    shim = python3_shim(tmp)
    if shim:
        env["PATH"] = str(shim) + os.pathsep + os.environ.get("PATH", "")
    if os.environ.get("CLAUDECODE"):  # nested in a Claude Code session: the SDK's CLI needs a clean environment
        for k in [k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC"))]:
            os.environ.pop(k, None)
    status, error = "ok", None
    try:
        result, commands = asyncio.run(run_skill(tmp, names, slot, model, trace, env))
        if result is not None:
            trace.event("model_summary", model=model, turns=result.num_turns, seconds=round(result.duration_ms / 1000),
                        cost_usd=result.total_cost_usd, is_error=result.is_error)
    except Exception as e:  # noqa: BLE001 - the trace and the export still get written
        status, error, commands = "error", f"{type(e).__name__}: {e}"[:400], []
    blob = "\n".join(commands)
    trace.event("check", name="blind", no_run_cases="run_cases" not in blob, no_practice_session="practice" not in blob,
                no_simulation="run_simulation" not in blob and "/simulations" not in blob)
    log = tmp / "chat_log.jsonl"
    for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
        c = json.loads(line)
        ok = all(not str(r.get("agent") or "").startswith("[error") for r in c.get("turns") or [])
        trace.event("self_test", kind="chat", name=c.get("name"), ok=ok, call_id=c.get("call_id"),
                    now=(c.get("variables") or {}).get("now"), turns=c.get("turns"))
    state = json.loads((tmp / STATE).read_text(encoding="utf-8")) if (tmp / STATE).exists() else {}
    ids = state.get(slot) or {}
    if (tmp / STATE).exists():
        mine = json.loads((KIT / STATE).read_text(encoding="utf-8")) if (KIT / STATE).exists() else {}
        mine.update({slot: ids} if ids else {})
        (KIT / STATE).write_text(json.dumps(mine, indent=1), encoding="utf-8")
    wf_file = tmp / "out" / f"{slot}.workflow.json"
    if wf_file.exists():
        wf = json.loads(wf_file.read_text(encoding="utf-8"))
        tools = sum(len(n.get("tools") or []) + len(n.get("pre_actions") or []) for n in (wf.get("nodes") or {}).values())
        trace.event("freya", workflow_id=ids.get("workflow_id"), agent_id=ids.get("agent_id"),
                    nodes=len(wf.get("nodes") or {}), tools=tools + len(wf.get("tools") or []))
    if ids.get("workflow_id"):
        import freya
        (KIT / "submission").mkdir(exist_ok=True)
        out = KIT / "submission" / f"{slot}.workflow.json"
        try:
            freya.export_workflow(ids["workflow_id"], str(out))
            trace.event("export", path=str(out.relative_to(KIT)))
        except Exception as e:  # noqa: BLE001
            status, error = "error", f"export failed: {e}"[:300]
    else:
        status, error = "error", error or "the skill saved no workflow for slot " + slot
    trace.event("run_end", status=status, error=error, seconds=round(time.time() - t0, 1),
                workflow_id=ids.get("workflow_id"), agent_id=ids.get("agent_id"))
    print(f"\nplanz {status}: slot {slot}, workflow {ids.get('workflow_id')}, agent {ids.get('agent_id')}, "
          f"{time.time() - t0:.0f} s, trace {trace.path}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
