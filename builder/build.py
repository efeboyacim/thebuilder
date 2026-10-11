#!/usr/bin/env python3
"""Build a Freya phone agent from a bank brief and its tool API file.

    python3 builder/build.py <brief_path> <tool_api_path>
    python3 builder/build.py <base brief> <later brief> <tool_api_path>   # a brief that builds on another

Input files are classified by content: the tool API is the JSON object with an `openapi` key; every other file is
brief material, read in the order given. Options:

    --slot S        workflow slot (else env BUILDER_SLOT, else 'hub-' + the brief names)
    --until STAGE   stop after ingest, toolapi, spec or compile
    --spec FILE     compile this spec instead of building one with the LLM (test fixtures only)

Writes traces/<slot>-<UTC time>.jsonl (env BUILDER_TRACE_DIR moves it) and echoes every trace event to stdout.
Artifacts (digest, spec, checks) go to out/<slot>/ (env BUILDER_OUT_DIR moves out/).
"""
import argparse, json, os, platform, subprocess, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if sys.path and Path(sys.path[0] or ".").resolve() == ROOT / "builder":
    sys.path[0] = str(ROOT)  # import as the `builder` package; builder/trace.py must not shadow stdlib `trace`

from builder import compiler, ingest, specgen, toolapi  # noqa: E402
from builder import spec as sp  # noqa: E402
from builder.trace import Trace  # noqa: E402

STAGES = ("ingest", "toolapi", "spec", "compile", "freya")
FALLBACK_SLOT = "build"


class StageNotReady(Exception):
    pass


def parse_args(argv):
    p = argparse.ArgumentParser(prog="build.py", description="Build a Freya phone agent from a brief and its tool API file.")
    p.add_argument("inputs", nargs="+", metavar="FILE", help="brief files (in order) and the tool API file")
    p.add_argument("--slot", help="workflow slot; else env BUILDER_SLOT, else 'hub-' + the brief names")
    p.add_argument("--until", choices=STAGES[:-1], help="stop after this stage")
    p.add_argument("--spec", metavar="SPEC_JSON", help="compile this spec instead of building one (test fixtures only)")
    return p.parse_args(argv)


def git_state():
    """(commit, dirty) of the builder checkout; ('unknown', None) outside git."""
    try:
        run = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, timeout=10)
        head = run("rev-parse", "--short=12", "HEAD")
        if head.returncode:
            return "unknown", None
        return head.stdout.strip(), bool(run("status", "--porcelain", "--", "builder").stdout.strip())
    except Exception:  # noqa: BLE001 - the trace must not fail on a missing git
        return "unknown", None


def fallback_slot(cli_slot, environ):
    for s in (cli_slot, environ.get("BUILDER_SLOT")):
        if s:
            try:
                return ingest.sanitize_slot(s)
            except ingest.InputError:
                pass
    return FALLBACK_SLOT


def display_path(path):
    """Relative to the working directory when the path is below it."""
    try:
        rel = os.path.relpath(path)
    except ValueError:  # another drive on Windows
        return path
    return path if rel.startswith("..") else rel


def ingest_summary(inputs):
    return {
        "briefs": [{"path": b.path, "bytes": b.bytes, "encoding": b.encoding, "headings": len(b.sections),
                    "sections": [t for lvl, t in b.sections if lvl <= 2]} for b in inputs.briefs],
        "tool_api": {"path": inputs.tool_api.path, **ingest.tool_api_summary(inputs.tool_api)},
        "cases": inputs.cases.path if inputs.cases else None,
    }


def stage_toolapi(ctx):
    api = toolapi.parse(ctx["inputs"].tool_api.data)
    ctx["api"] = api
    return {"server": api.server, "tools": {
        n: {"method": t.method, "path": t.path, "required": [p for p, f in t.request.items() if f.required],
            "request_fields": len(t.request), "response_fields": len(t.response), "error_statuses": sorted(t.error_statuses)}
        for n, t in api.tools.items()}}


def stage_spec(ctx):
    args, trace, api = ctx["args"], ctx["trace"], ctx["api"]
    out_dir = os.path.join(ctx["out_root"], ctx["slot"])
    if args.spec:
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
        report = sp.validate(spec, api, "\n".join(b.text for b in ctx["inputs"].briefs))
        trace.event("check", name="spec_validate_given", ok=report.ok, errors=report.errors[:60],
                    n_errors=len(report.errors), warnings=report.warnings[:20])
        if report.errors:
            raise sp.SpecError(f"{args.spec}: {len(report.errors)} validation error(s); first: {report.errors[0]}")
        summary = {"given": args.spec, **report.info}
    else:
        try:
            spec, report, summary = specgen.run(ctx, out_dir)
        except specgen.SpecGenError as e:  # best so far: only indexing, quoting or wording errors left
            if e.spec is None or not e.report or not all(specgen.soft(x) for x in e.report.errors):
                raise
            for x in e.report.errors:
                trace.warning(f"spec kept with a soft error: {x}")
            spec, report, summary = e.spec, e.report, e.summary
    for path in report.info.get("not_specified") or []:
        trace.warning(f"not_specified: {path} (neutral behaviour)")
    for stage in report.info.get("hangup_chosen_by_model") or []:
        trace.warning(f"hang-up situation in stage {stage} is not stated in the brief; the model chose the closest one")
    trace.event("spec", **summary)
    ctx["spec"] = spec
    return {"out": out_dir, "rules": summary.get("rules"), "coverage": summary.get("coverage"),
            "repair_rounds": summary.get("repair_rounds"), "not_specified": len(report.info.get("not_specified") or []),
            "residuals": report.info.get("residuals")}


def stage_compile(ctx):
    trace, spec, api = ctx["trace"], ctx["spec"], ctx["api"]
    wf = compiler.compile_workflow(spec, api)  # raises on a lint error
    out_dir = Path(ctx["out_root"]) / ctx["slot"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "workflow.json"
    path.write_text(json.dumps(wf, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    s = compiler.summary(wf)
    trace.event("self_test", name="graph_lint", result="pass", details="no unknown target, unreachable node, merged "
                "transition, open passthrough, null, const or doubled brace in code")
    trace.event("self_test", name="brain_code", result="pass", details=f"{s['brain_bytes']} bytes, compiles, one "
                "function, no doubled braces")
    ctx["workflow"] = wf
    return {"workflow": display_path(str(path)), **s}


def stage_freya(ctx):
    """Dry run, save and publish into the slot, read back and diff, export to submission/<slot>.workflow.json."""
    trace, wf, slot = ctx["trace"], ctx["workflow"], ctx["slot"]
    if not os.environ.get("FREYA_API_KEY"):
        raise RuntimeError("FREYA_API_KEY is not set (source env.sh)")
    import freya
    s = compiler.summary(wf)
    t0 = time.time()
    v = freya.validate_workflow(f"{slot} dry run", wf)
    trace.event("freya", action="validate", ok=True, seconds=round(time.time() - t0, 1),
                result=sorted(v) if isinstance(v, dict) else str(v)[:200], nodes=s["nodes"], tools=s["tools"])
    state_file = ROOT / ".freya-agents.json"
    known = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    saves = 1 if slot in known else 2  # a new workflow gets its event model only with its first update
    for n in range(saves):
        t0 = time.time()
        st = freya.save_workflow(slot, wf, state_file=str(state_file))
        trace.event("freya", action="update" if (slot in known or n) else "create", workflow_id=st["workflow_id"],
                    agent_id=st["agent_id"], version=st.get("version"), published=bool(st.get("version")),
                    seconds=round(time.time() - t0, 1), nodes=s["nodes"], tools=s["tools"])
    got = freya.api("GET", f"/api/v2/workflow/{st['workflow_id']}", workspaceId=freya.workspace_id())["workflow"]
    diffs = compiler.readback_diff(wf, got.get("workflow") or {})
    trace.event("freya", action="readback", workflow_id=st["workflow_id"], agent_id=st["agent_id"],
                nodes=len((got.get("workflow") or {}).get("nodes") or {}), diffs=diffs)
    trace.event("self_test", name="readback_diff", result="pass" if not diffs else "fail", details=diffs[:20])
    sub = ROOT / "submission"
    sub.mkdir(exist_ok=True)
    path = freya.export_workflow(st["workflow_id"], str(sub / f"{slot}.workflow.json"))
    trace.event("freya", action="export", workflow_id=st["workflow_id"], agent_id=st["agent_id"],
                path=display_path(path))
    ctx["outputs"].update(workflow_id=st["workflow_id"], agent_id=st["agent_id"], version=st.get("version"),
                          export=display_path(path))
    return {"workflow_id": st["workflow_id"], "agent_id": st["agent_id"], "version": st.get("version"),
            "export": display_path(path), "readback_diffs": len(diffs)}


STAGE_FUNCS = {"toolapi": stage_toolapi, "spec": stage_spec, "compile": stage_compile, "freya": stage_freya}


def run_stages(ctx):
    """Run the stages after ingest, each traced. ctx["done"] is the last finished stage."""
    args, trace = ctx["args"], ctx["trace"]
    for name in STAGES[1:]:
        if args.until and STAGES.index(args.until) < STAGES.index(name):
            break
        fn = STAGE_FUNCS.get(name)
        if fn is None:
            raise StageNotReady(f"stage {name} is not implemented yet")
        t0 = time.time()
        try:
            summary = fn(ctx)
        except Exception as e:
            trace.event("stage", name=name, ok=False, seconds=round(time.time() - t0, 1), error=f"{type(e).__name__}: {e}")
            raise
        trace.event("stage", name=name, ok=True, seconds=round(time.time() - t0, 1), summary=summary)
        ctx["done"] = name


def print_summary(trace, slot, inputs, status, done, error, outputs=None):
    out = ["", "== build summary =="]
    secs = round(time.time() - trace.start, 1)
    state = {"ok": "ok", "stopped": f"stopped after {done}", "error": f"error after {done or 'start'}"}[status]
    out.append(f"status       {state} ({secs} s)")
    if error:
        out.append(f"error        {error}")
    out.append(f"slot         {slot}")
    if inputs:
        for n, b in enumerate(inputs.briefs, 1):
            out.append(f"brief {n}      {b.path}  {b.bytes} bytes, {b.encoding}, {len(b.sections)} headings")
        s = ingest.tool_api_summary(inputs.tool_api)
        out.append(f"tool API     {inputs.tool_api.path}  openapi {s['openapi']}, {s['operations']} operations, "
                   f"server {', '.join(s['servers']) or 'none'}")
        out.append(f"cases        {inputs.cases.path if inputs.cases else 'none'}")
    tests = [e for e in trace.events if e["ev"] == "self_test"]
    passed = sum(1 for e in tests if e.get("result") == "pass")
    for k in ("workflow_id", "agent_id", "version", "export"):
        if (outputs or {}).get(k) is not None:
            out.append(f"{k:<12} {outputs[k]}")
    out.append(f"model calls  {trace.count('model_call')}")
    out.append(f"self-tests   {len(tests)} ({passed} pass)")
    out.append(f"warnings     {trace.count('warning')}")
    out.append(f"trace        {trace.path}")
    print("\n".join(out), flush=True)


def main(argv=None, trace_dir=None, environ=None):
    environ = os.environ if environ is None else environ
    args = parse_args(argv)
    trace = Trace()
    inputs, error, done = None, None, None
    t0 = time.time()
    try:
        if args.spec and not os.path.isfile(args.spec):
            raise ingest.InputError(f"--spec {args.spec}: no such file")
        inputs = ingest.ingest(args.inputs)
        slot = ingest.resolve_slot(args.slot, environ, inputs.briefs)
    except ingest.InputError as e:
        error, slot = str(e), fallback_slot(args.slot, environ)

    trace.open(display_path(str(trace_dir or environ.get("BUILDER_TRACE_DIR") or ROOT / "traces")), slot)
    commit, dirty = git_state()
    trace.event("run_start", t=trace.start, argv=sys.argv[:1] + list(argv if argv is not None else sys.argv[1:]),
                slot=slot, inputs=[i.record() for i in inputs.all] if inputs else [], until=args.until,
                spec=args.spec, builder_commit=commit, builder_dirty=dirty, python=platform.python_version(),
                platform=sys.platform)
    if error:
        trace.event("stage", name="ingest", ok=False, seconds=round(time.time() - t0, 1), error=error)
        status = "error"
    else:
        for w in inputs.warnings:
            trace.warning(w)
        trace.event("stage", name="ingest", ok=True, seconds=round(time.time() - t0, 1), summary=ingest_summary(inputs))
        ctx = {"args": args, "inputs": inputs, "trace": trace, "slot": slot, "done": "ingest", "outputs": {},
               "out_root": environ.get("BUILDER_OUT_DIR") or str(ROOT / "out")}
        try:
            run_stages(ctx)
            status = "stopped" if args.until else "ok"
        except Exception as e:  # noqa: BLE001 - every failure ends in a run_end event
            error, status = f"{type(e).__name__}: {e}", "error"
            if not isinstance(e, (StageNotReady, toolapi.ToolAPIError, sp.SpecError, specgen.SpecGenError,
                                  compiler.CompileError)):
                trace.event("error", text=error, traceback=traceback.format_exc()[-2000:])
        done = ctx["done"]
    outputs = {"trace": trace.path, **(ctx["outputs"] if inputs else {})}
    trace.close(status, stopped_after=done if status == "stopped" else None, error=error, outputs=outputs)
    print_summary(trace, slot, inputs, status, done, error, outputs)
    return 1 if status == "error" else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - an exotic stdout keeps its own encoding
        pass
    sys.exit(main())
