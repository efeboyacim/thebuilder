"""Scripted chat test of a saved workflow over Freya's chat route: one customer line per turn, then the call's
tool calls and results. Used by the case builder for self-tests; it never edits the workflow.

    python builder/chat_probe.py <workflow_id> --vars '{"now": "..."}' "first customer line" "second line" ...

Prints `AGENT: ...` per turn and finally every tool call with its arguments and result (status, body head).
"""
import argparse, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya  # noqa: E402


def last_agent_line(call_id):
    try:
        msgs = freya.get_call(call_id)["messages"]
    except Exception:  # noqa: BLE001
        return ""
    lines = [m.get("message") or "" for m in msgs if m.get("role") == "assistant"]
    return lines[-1] if lines else ""


def tool_events(call_id):
    out = []
    try:
        msgs = freya.get_call(call_id)["messages"]
    except Exception as e:  # noqa: BLE001
        return [f"(could not read call: {e})"]
    for m in msgs:
        if m.get("role") in ("user", "assistant"):
            continue
        try:
            e = json.loads(m.get("message") or "{}")
        except ValueError:
            continue
        ev, fn = e.get("event"), e.get("function")
        if fn == "code_exec":
            continue
        if ev == "function_call_start":
            out.append(f"CALL {fn} {json.dumps(e.get('args'), ensure_ascii=False)[:300]}")
        elif ev == "function_call_complete":
            r = e.get("result") or {}
            body = r.get("data") if r.get("data") is not None else r.get("message")
            out.append(f"  -> {r.get('status')} {r.get('status_code')} {json.dumps(body, ensure_ascii=False)[:300]} {r.get('error') or ''}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("workflow_id")
    p.add_argument("--vars", default="{}", help="call variables as JSON")
    p.add_argument("turns", nargs="+")
    a = p.parse_args(argv)
    variables, call_id = json.loads(a.vars), None
    for line in a.turns:
        print(f"CUSTOMER: {line}", flush=True)
        body = {"workflowId": a.workflow_id, "message": line, "customerVariables": variables}
        if call_id:
            body["callId"] = call_id
        try:
            r = freya.api("POST", "/api/v2/chat/message", body)
            call_id = r.get("callId") or call_id
            reply = r.get("response") or ""
        except Exception as e:  # noqa: BLE001 - a 504 still finishes the turn; read it from the call
            if not call_id:
                print(f"ERROR first turn: {str(e)[:300]}", flush=True)
                return 1
            time.sleep(5)
            reply = last_agent_line(call_id) + "  (read from call after: " + str(e)[:60] + ")"
        print(f"AGENT: {reply}", flush=True)
    if call_id:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
        print(f"call_id {call_id}")
        print("\n".join(tool_events(call_id)))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
