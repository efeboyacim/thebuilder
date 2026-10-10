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
"""
import argparse, os, platform, subprocess, sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if sys.path and Path(sys.path[0] or ".").resolve() == ROOT / "builder":
    sys.path[0] = str(ROOT)  # import as the `builder` package; builder/trace.py must not shadow stdlib `trace`

from builder import ingest, toolapi  # noqa: E402
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


STAGE_FUNCS = {"toolapi": stage_toolapi}


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


def print_summary(trace, slot, inputs, status, done, error):
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
        ctx = {"args": args, "inputs": inputs, "trace": trace, "slot": slot, "done": "ingest"}
        try:
            run_stages(ctx)
            status = "stopped" if args.until else "ok"
        except Exception as e:  # noqa: BLE001 - every failure ends in a run_end event
            error, status = f"{type(e).__name__}: {e}", "error"
            if not isinstance(e, (StageNotReady, toolapi.ToolAPIError)):
                trace.event("error", text=error, traceback=traceback.format_exc()[-2000:])
        done = ctx["done"]
    trace.close(status, stopped_after=done if status == "stopped" else None, error=error,
                outputs={"trace": trace.path})
    print_summary(trace, slot, inputs, status, done, error)
    return 1 if status == "error" else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - an exotic stdout keeps its own encoding
        pass
    sys.exit(main())
