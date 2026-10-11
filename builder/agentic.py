"""Case mode: `build.py <case_folder> <mock_url>`. A Claude Agent SDK run reads the whole case folder, writes the
service contract (OpenAPI) for the mock, writes and saves the workflow, self-tests it with scripted chats against the
mock and exports it. The instructions are generic (`builder/case_builder.md`); nothing of a case is in code.

Every assistant turn (model + one-line purpose), tool use and self-test goes to the build trace.
"""
import asyncio, os, pathlib, re, time

from builder import llm

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTRUCTIONS = ROOT / "builder" / "case_builder.md"
MAX_TURNS = 200


def is_case_mode(paths):
    return (len(paths) == 2 and os.path.isdir(paths[0]) and re.match(r"https?://", paths[1] or "") is not None)


def run(case_dir, mock_url, slot, trace, out_root):
    out = pathlib.Path(out_root) / slot
    out.mkdir(parents=True, exist_ok=True)
    llm.clear_host_session_env()
    os.environ.setdefault("PYTHONUTF8", "1")
    import claude_agent_sdk as sdk
    model = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
    rel = lambda p: os.path.relpath(p, ROOT).replace("\\", "/")
    prompt = (INSTRUCTIONS.read_text(encoding="utf-8") + "\n\n## This run\n\n"
              f"- case folder: `{rel(case_dir)}`\n- mock base URL: `{mock_url.rstrip('/')}`\n"
              f"- slot: `{slot}`\n- output folder: `{rel(out)}`\n"
              "- Python on this machine is `python` (not `python3`); use Bash for commands.\n")
    options = sdk.ClaudeAgentOptions(
        cwd=str(ROOT), model=model, setting_sources=[],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"], permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        env={"BASH_DEFAULT_TIMEOUT_MS": "600000", "BASH_MAX_TIMEOUT_MS": "900000",
             "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1", "PYTHONUTF8": "1"})
    trace.event("model_call", model=model, purpose="case builder agent: read case, write contract and workflow, test, export")
    state = {"result": None}

    async def go():
        async for m in sdk.query(prompt=prompt, options=options):
            if isinstance(m, sdk.AssistantMessage):
                for b in m.content:
                    if isinstance(b, sdk.TextBlock):
                        txt = b.text.strip()
                        print(txt, flush=True)
                        trace.event("agent_text", model=getattr(m, "model", model), text=txt[:2000])
                        hit = re.search(r"RESULT workflow_id=(\S+) agent_id=(\S+) tests=(\S+)", txt)
                        if hit:
                            state["result"] = hit.groups()
                    elif isinstance(b, sdk.ToolUseBlock):
                        inp = b.input if isinstance(b.input, dict) else {}
                        what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                        print(f"  [{b.name}] {str(what)[:160]}", flush=True)
                        trace.event("tool_use", tool=b.name, input=str(what)[:500])
                        if b.name == "Bash" and "chat_probe.py" in str(what):
                            trace.event("self_test", kind="chat_probe", command=str(what)[:500], result="ran")
            elif isinstance(m, sdk.ResultMessage):
                trace.event("agent_done", turns=m.num_turns, seconds=round((m.duration_ms or 0) / 1000),
                            cost_usd=m.total_cost_usd, is_error=m.is_error)
                print(f"agent done: {m.num_turns} turns, {(m.duration_ms or 0) / 1000:.0f} s", flush=True)

    t0 = time.time()
    asyncio.run(go())
    if not state["result"]:
        raise RuntimeError("case builder ended without a RESULT line")
    wf, agent, tests = state["result"]
    export = f"submission/{slot}.workflow.json"
    if not (ROOT / export).is_file():
        raise RuntimeError(f"case builder did not export {export}")
    return {"workflow_id": wf, "agent_id": agent, "tests": tests, "export": export,
            "seconds": round(time.time() - t0)}
