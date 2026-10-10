"""Spikes S1 + S1b (item 03, throwaway): the hub-and-spoke loop on the chat API.

    python3 spikes/s1_loop.py [--slot scratch] [--only s1|s1b] [--keep]

Builds a small talk -> brain -> talk workflow, saves it to the slot, drives it turn by turn through
POST /api/v2/chat/message, reads the call back and prints PASS/FAIL per behaviour. S1b repeats a shorter
run with the brain padded to 60 KB. At the end the slot gets the base agent back, so `scratch` stays the
control agent (skip that with --keep). Results go to spikes/results/.
"""
import argparse, json, pathlib, re, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya

OUT = ROOT / "spikes" / "results"
NOW = "2026-11-25T10:00"          # a Wednesday, far from the real clock, so a date anchor shows which clock it used
CV = {"customer_id": "c_spike", "first_name": "Dana", "last_name": "Reed", "title": "Ms", "now": NOW}
MARK = "DIALOGUE-IN-"             # a talk node's own dialogue call would say this

# ---------------------------------------------------------------- workflow

INIT = """
vars['init_ran'] = 'yes'
vars['farewell'] = 'Default farewell.'
vars['say_next'] = ''
vars['state'] = ''
"""

HOP = """
try:
    vars['hop_count'] = str(int(vars.get('hop_count') or 0) + 1)
except Exception:
    vars['hop_count'] = 'error'
"""

CRASH = """
vars['before_raise'] = 'yes'
raise ValueError('deliberate spike raise')
"""

BRAIN = r"""
import json, re
try:
    raw = vars.get('state')
    st = json.loads(raw) if isinstance(raw, str) and raw else dict()
    st['runs'] = st.get('runs', 0) + 1
    route, say = 'talk_b', ''
    if st.get('hops_left', 0) > 0:
        st['hops_left'] -= 1
        if st['hops_left'] == 0:
            route = 'talk_a'
            say = 'Hopped. Hop count ' + str(vars.get('hop_count')) + ', brain runs ' + str(st['runs']) + '.'
        else:
            route = 'hop'
    else:
        said = str(vars.get('said') or '')
        m = re.search(r'\b(ping|again|hop|code|check|date|crash|bye)\b', said.lower())
        word = m.group(1) if m else str(vars.get('cmd') or 'other')
        st['turn'] = st.get('turn', 0) + 1
        tag = str(st['turn']) + ', brain runs ' + str(st['runs'])
        any_ = str(vars.get('code_any') if vars.get('code_any') is not None else '<unset>')
        last = str(vars.get('code_last') if vars.get('code_last') is not None else '<unset>')
        if word == 'ping':
            route, say = 'talk_a', 'Pong ' + tag + '.'
        elif word == 'again':
            route, say = 'talk_a', 'Again ' + tag + '.'
        elif word == 'hop':
            st['hops_left'] = 12
            vars['hop_count'] = '0'
            route = 'hop'
        elif word == 'code':
            route, say = 'talk_a', 'Code noted, any ' + any_ + ', last ' + last + ', turn ' + tag + '.'
            vars['code_any'] = ''
            vars['code_last'] = ''
        elif word == 'check':
            route, say = 'talk_a', 'Slots now, any ' + any_ + ', last ' + last + ', turn ' + tag + '.'
        elif word == 'date':
            route, say = 'talk_a', 'Date slot ' + str(vars.get('when_date')) + ', turn ' + tag + '.'
        elif word == 'crash':
            route = 'crasher'
        elif word == 'bye':
            vars['farewell'] = 'Custom farewell for ' + str(vars.get('first_name')) + ' after turn ' + tag + '.'
            route = 'end_custom'
        else:
            route, say = 'talk_b', 'Unknown command ' + said[:60] + ', turn ' + tag + '.'
    vars['route'] = route
    vars['say_next'] = say
    vars['brain_runs'] = str(st['runs'])
    vars['state'] = json.dumps(st)
except Exception as e:
    vars['route'] = 'error'
    vars['say_next'] = 'Brain error.'
    vars['brain_error'] = repr(e)[:200]
"""

EXTRACT = [
    {"name": "said", "type": "string", "description": "The customer's LAST message, copied verbatim.", "min_confidence": 0.1},
    {"name": "cmd", "type": "string", "enum_values": ["ping", "again", "hop", "code", "check", "date", "crash", "bye", "other"],
     "description": "The command word in the customer's LAST message; other if there is none.", "min_confidence": 0.1},
    {"name": "code_any", "type": "string", "description": "The 4-digit code the customer gave.", "min_confidence": 0.1},
    {"name": "code_last", "type": "string",
     "description": "The 4-digit code in the customer's LAST message only; none if that message has no code.", "min_confidence": 0.1},
    {"name": "when_date", "type": "date", "description": "The day the customer names in their LAST message.", "min_confidence": 0.1},
]


def always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def when(var, op, operand, target):
    return {"target_node": target, "description": f"{var} {op} {operand}",
            "condition": {"logic": "or", "conditions": [{"variable": var, "operator": op, "operand": operand}]}}


def code_node(name, code, transitions):
    assert "{{" not in code and "}}" not in code, name
    fn = {"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec", "parameters": {
        "type": "object", "properties": {"code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 10000}}}}}
    return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": transitions[-1]["target_node"],
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [fn], "transitions": transitions}


def talk_node(name, text):
    return {"name": name, "node_type": "conversation", "on_enter": {"type": "tts", "text": text},
            "task_message": f"Say exactly: {MARK}{name}. No tool call is allowed.", "extraction_schema": EXTRACT,
            "max_node_turns": 200, "disable_inactivity_monitor": True, "transitions": [always("brain")]}


def pad(kb):
    """Dead but valid Python, so S1b pays the parse cost of a big brain."""
    lines, i = ["# PAD_MARKER_S1B"], 0
    while sum(len(l) + 1 for l in lines) < kb * 1024:
        lines.append(f"def _pad_{i}(x, y=0):\n    z = x * {i % 97} + y - {i % 13}\n    return [z, str(z), z % 7]")
        i += 1
    return "\n".join(lines) + "\n"


def workflow(pad_kb=0):
    brain = (pad(pad_kb) if pad_kb else "") + BRAIN
    nodes = {
        "init": code_node("init", INIT, [always("opening")]),
        "opening": talk_node("opening", "Spike opening. Init ran {{init_ran}}. Send a command."),
        "brain": code_node("brain", brain, [
            when("route", "equals", "talk_a", "talk_a"),
            when("route", "equals", "talk_b", "talk_b"),
            when("route", "equals", "hop", "hop"),
            when("route", "equals", "crasher", "crasher"),
            when("route", "equals", "end_custom", "end_custom"),
            always("talk_fallback")]),
        "hop": code_node("hop", HOP, [always("brain")]),
        "talk_a": talk_node("talk_a", "{{say_next}}"),
        "talk_b": talk_node("talk_b", "{{say_next}}"),
        "talk_fallback": talk_node("talk_fallback", "Fallback. Route was {{route}}."),
        "crasher": code_node("crasher", CRASH, [when("crasher_failure_count", "greater_than", 0, "talk_err"), always("talk_after_crash")]),
        "talk_err": talk_node("talk_err", "Crash caught. Failures {{crasher_failure_count}}, status {{crasher_response.status}}, before {{before_raise}}."),
        "talk_after_crash": talk_node("talk_after_crash", "No failure seen. Status {{crasher_response.status}}, before {{before_raise}}."),
        "end_custom": {"name": "end_custom", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                       "transitions": [], "terminal": {"type": "function", "function": {
                           "name": "spike_custom_end", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {
                               "message_mode": {"type": "string", "default": "custom"},
                               "spoken_message": {"type": "string", "default": "{{farewell}}"}}}}}},
    }
    return {"initial_node": "init", "system_prompt": "You are a test harness. Say only what your instructions give you, word for word.",
            "nodes": nodes, "global_nodes": {}}


# ---------------------------------------------------------------- chat driver

def reply_to(cid, n):
    """The first assistant message after the n-th user message, or None. A turn that hit a 504 logs no
    turn_completed event, so this looks at the messages themselves."""
    users = 0
    for m in freya.get_call(cid).get("messages") or []:
        if m.get("role") == "user":
            users += 1
        elif m.get("role") == "assistant" and users == n:
            return m.get("message")
    return None


def chat(wid, turns):
    """Send the turns one by one. Returns (call_id, rows) with response text and wall-clock seconds per turn.

    A 504 from the chat route does not stop the engine: the turn goes on in the background. Then this waits
    (polling the call every 5 s, at most 200 s) until that turn completes, so the next message does not overlap it."""
    cid, rows = None, []
    for n, msg in enumerate(turns, 1):
        body = {"workflowId": wid, "message": msg}
        body.update({"callId": cid} if cid else {"customerVariables": CV})
        t = time.time()
        try:
            r = freya.api("POST", "/api/v2/chat/message", body)
            cid = cid or r.get("callId")
            rows.append({"user": msg, "agent": r.get("response", ""), "s": round(time.time() - t, 2)})
        except Exception as e:
            row = {"user": msg, "agent": None, "s": round(time.time() - t, 2), "error": str(e)[:200]}
            if cid and "504" in str(e):
                while time.time() - t < 200:
                    reply = reply_to(cid, n)
                    if reply is not None:
                        row.update(agent=reply, completed_s=round(time.time() - t, 1))
                        break
                    time.sleep(5)
            rows.append(row)
        r = rows[-1]
        print(f"  {r['s']:6.2f}s  > {msg}\n          < {r.get('agent')}" + (f"   [{r['error'][:60]}; completed after {r.get('completed_s')} s]" if r.get("error") else ""), flush=True)
        if "Custom farewell" in str(r.get("agent")) or (r.get("error") and r.get("agent") is None):
            break
    if cid:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": cid})
        except Exception as e:
            print("  chat/end:", str(e)[:200])
    return cid, rows


def events(call):
    """Split the call's messages into turns: each user message starts one. System events are parsed."""
    turns, cur = [], None
    for m in call.get("messages") or []:
        role, text = m.get("role"), m.get("message")
        if role == "user":
            cur = {"user": text, "t0": m.get("secondsFromStart"), "assistant": [], "nodes": [], "errors": [], "events": [], "code_s": []}
            turns.append(cur)
            continue
        if cur is None:
            cur = {"user": None, "t0": 0, "assistant": [], "nodes": [], "errors": [], "events": [], "code_s": []}
            turns.append(cur)
        cur.setdefault("code_s", [])
        if role == "assistant":
            cur["assistant"].append({"node": m.get("nodeId"), "text": text})
        elif role == "system":
            try:
                ev = json.loads(text)
            except Exception:
                continue
            ev["_t"] = m.get("secondsFromStart")
            cur["events"].append(ev)
            e = ev.get("event")
            if e == "node_enter":
                cur["nodes"].append((ev.get("node"), round(ev["_t"], 2)))
            elif e in ("error", "function_call_error"):
                cur["errors"].append(str(ev.get("error") or ev.get("message") or ev.get("details"))[:160])
            if e == "function_call_start" and ev.get("function") == "code_exec":
                cur["_start"] = ev["_t"]
            elif e in ("function_call_complete", "function_call_error") and ev.get("function") == "code_exec" and "_start" in cur:
                cur["code_s"].append(round(ev["_t"] - cur.pop("_start"), 2))
    return turns


def sandbox_down(turn):
    return bool(turn) and any("snekbox" in e or "Failed to reach" in e for e in turn["errors"])


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def last_vars(turn):
    for ev in reversed(turn["events"]):
        if isinstance(ev.get("variable_table"), dict):
            return ev["variable_table"]
    return {}


# ---------------------------------------------------------------- checks

def check(name, ok, detail, needs=()):
    """PASS/FAIL per behaviour, INFO for probes. INVALID when a turn it needs is missing or hit the sandbox outage."""
    if any(t is None or sandbox_down(t) for t in needs):
        status = "INVALID"
    else:
        status = "PASS" if ok is True else "FAIL" if ok is False else "INFO"
    print(f"{status:7}  {name}: {detail}")
    return {"behaviour": name, "status": status, "detail": detail}


def hop_seconds(turn):
    """Mean seconds between consecutive brain/hop node entries in one turn (one code run each)."""
    ts = [t for n, t in turn["nodes"] if n in ("brain", "hop")]
    return round((ts[-1] - ts[0]) / (len(ts) - 1), 2) if len(ts) > 1 else None


def converse(wid, script):
    """Run the script; return the call, the rows and accessors by script index (the same text can occur twice)."""
    cid, rows = chat(wid, script)
    call = freya.get_call(cid)
    turns = [t for t in events(call) if t["user"] is not None]
    say = lambda i: str(rows[i].get("agent")) if i < len(rows) else "<not sent>"
    secs = lambda i: rows[i].get("s") if i < len(rows) else None
    turn = lambda i: turns[i] if i < len(turns) and turns[i]["user"] == script[i] else None
    return cid, call, rows, turns, say, secs, turn


def summary(cid, call, rows, turns):
    ok_runs = [s for t in turns if not sandbox_down(t) for s in t["code_s"]]
    return {"call_id": cid, "rows": rows, "messages_bytes": len(json.dumps(call.get("messages"))), "ended_reason": call.get("endedReason"),
            "code_run_s": {"median": median(ok_runs), "max": max(ok_runs) if ok_runs else None, "n": len(ok_runs)},
            "sandbox_down_from_turn": next((i for i, t in enumerate(turns) if sandbox_down(t)), None),
            "turns": [{"user": t["user"], "nodes": t["nodes"], "code_s": t["code_s"], "errors": t["errors"][:3]} for t in turns]}


def brain_entries(t):
    return sum(1 for n, _ in t["nodes"] if n == "brain")


def run_probe(wid):
    script = ["Hello?", "ping"]
    cid, call, rows, turns, say, secs, turn = converse(wid, script)
    res = summary(cid, call, rows, turns)
    t1 = turn(1)
    res["checks"] = [check("Sandbox up; code run time", say(1).startswith("Pong 1"),
                           f"{say(1)} | code runs {t1['code_s'] if t1 else None} s | errors {t1['errors'][:1] if t1 else None}")]
    return res, call


def run_s1(wid, hop=True):
    # The hop turn goes last: at ~5 s per code run its 25 runs outlast the chat route's 60 s, and nothing after it depends on it.
    # With hop=False its slot holds a plain ping, and D08 is reported as not run.
    script = ["Hello?", "ping", "again", "ping", "code 4321", "check", "check again, nothing new", "date tomorrow",
              "crash", "ping", "hop" if hop else "ping", "bye"]
    HELLO, PING, AGAIN, PING2, CODE, CHECK, CHECK2, DATE, CRASH_, PING3, HOPS, BYE = range(len(script))
    cid, call, rows, turns, say, secs, turn = converse(wid, script)
    res = summary(cid, call, rows, turns)
    plain = [turn(PING), turn(AGAIN), turn(PING2)]
    out = []
    out.append(check("D38 first code node (init) runs on the chat route", "Init ran yes" in say(HELLO),
                     say(HELLO) + (" (init skipped: literal placeholder)" if "{{init_ran}}" in say(HELLO) else "")))
    out.append(check("D03 loop core talk->brain->talk", say(PING).startswith("Pong 1"), say(PING), [turn(PING)]))
    out.append(check("D04 no dialogue call in a talk node whose always fired",
                     not any(MARK in str(r.get("agent")) for r in rows) and all(t and len(t["assistant"]) == 1 for t in plain),
                     f"marker in {[r['user'] for r in rows if MARK in str(r.get('agent'))]}; assistant messages per turn "
                     f"{[len(t['assistant']) for t in turns]}", plain))
    out.append(check("D07 say_next re-renders on re-entry of the same tts node", say(AGAIN).startswith("Again 2") and say(PING2).startswith("Pong 3"),
                     f"{say(AGAIN)} | {say(PING2)}", [turn(AGAIN), turn(PING2)]))
    out.append(check("D04 no cascade: one brain run per plain turn", all(t and brain_entries(t) == 1 for t in plain),
                     f"brain entries per turn {[brain_entries(t) for t in turns]}", plain))
    hop_row = rows[HOPS] if HOPS < len(rows) else {}
    if hop:
        out.append(check("D08 12-hop ping-pong in one turn", "Hop count 12" in say(HOPS),
                         f"{say(HOPS)} | turn {secs(HOPS)} s wall"
                         + (f" ({hop_row['error'][:40]}; completed after {hop_row.get('completed_s')} s)" if hop_row.get("error") else "")
                         + f", {hop_seconds(turn(HOPS)) if turn(HOPS) else None} s per code run", [turn(HOPS)]))
    else:
        out.append(check("D08 12-hop ping-pong in one turn", None, "not run in this pass (--no-hop)"))
    out.append(check("D12 code_last ('LAST message only') not refilled from an older message",
                     say(CODE).startswith("Code noted") and say(CHECK).startswith("Slots now") and "last 4321" not in say(CHECK) + say(CHECK2),
                     f"{say(CODE)} | {say(CHECK)} | {say(CHECK2)}", [turn(CODE), turn(CHECK), turn(CHECK2)]))
    out.append(check("D12 code_any (no 'LAST message' wording) not refilled",
                     say(CHECK).startswith("Slots now") and "any 4321" not in say(CHECK) + say(CHECK2),
                     f"{say(CHECK)} | {say(CHECK2)}", [turn(CHECK), turn(CHECK2)]))
    d = say(DATE)
    anchor = "now (2026-11-26)" if "2026-11-26" in d else "real clock (2026-10-11)" if "2026-10-11" in d else "other"
    out.append(check("D34 date-type anchor (probe)", None if d.startswith("Date slot") else False, f"{d} -> {anchor}", [turn(DATE)]))
    ct = turn(CRASH_)
    out.append(check("D14 a raising code node: the call survives and the failure is visible to routing",
                     say(CRASH_).startswith("Crash caught") and say(PING3).startswith("Pong"),
                     f"{say(CRASH_)} | errors {ct['errors'][:2] if ct else None} | next turn: {say(PING3)}", [ct, turn(PING3)]))
    out.append(check("Custom farewell from code; endedReason = terminal function name",
                     "Custom farewell for Dana" in say(BYE) and call.get("endedReason") == "spike_custom_end",
                     f"{say(BYE)} | endedReason {call.get('endedReason')}", [turn(BYE)]))
    out.append(check("Code run time (S1, 2 KB brain)", None,
                     f"{res['code_run_s']}; plain turn {secs(PING)} s wall, nodes {turn(PING)['nodes'] if turn(PING) else None}"))
    res["checks"] = out
    res["crash_vars"] = {k: v for k, v in last_vars(ct).items() if "crash" in k or "before" in k} if ct else {}
    return res, call


def run_s1b(wid, s1):
    # No hop turn here: S1 answers the hop question, and 25 runs of a 60 KB brain only load the shared sandbox.
    script = ["Hello?", "ping", "again", "ping", "bye"]
    HELLO, PING, AGAIN, PING2, BYE = range(len(script))
    cid, call, rows, turns, say, secs, turn = converse(wid, script)
    res = summary(cid, call, rows, turns)
    blob = json.dumps(call.get("messages"))
    starts = [m for m in call.get("messages") or [] if '"function_call_start"' in str(m.get("message")) and "PAD_MARKER_S1B" in str(m.get("message"))]
    s1_med = (s1 or {}).get("code_run_s", {}).get("median")
    res["checks"] = [
        check("S1b loop works with a 60 KB brain", say(PING).startswith("Pong 1") and say(AGAIN).startswith("Again 2") and say(PING2).startswith("Pong 3"),
              f"{say(PING)} | {say(AGAIN)} | {say(PING2)}", [turn(PING), turn(AGAIN), turn(PING2)]),
        check("S1b code run time <= 1.5 s", (res["code_run_s"]["median"] or 99) <= 1.5, f"{res['code_run_s']} vs S1 median {s1_med}",
              [turn(PING), turn(AGAIN), turn(PING2)]),
        check("S1b code kept out of the transcript", not starts, f"{len(starts)} function_call_start events carry the code"),
        check("S1b transcript size", None, f"{len(blob)} bytes for {len(turns)} turns vs S1 {(s1 or {}).get('messages_bytes')} bytes"),
        check("S1b custom farewell", "Custom farewell for Dana" in say(BYE) and call.get("endedReason") == "spike_custom_end",
              f"{say(BYE)} | endedReason {call.get('endedReason')}", [turn(BYE)]),
    ]
    return res, call


# ---------------------------------------------------------------- main

def save(slot, wf, label):
    v = freya.validate_workflow(f"spike {label}", wf)
    print(f"dry run {label}: {v}")
    s = freya.save_workflow(slot, wf)
    print(f"saved {label} to slot {slot}: workflow {s['workflow_id']} version {s.get('version')}")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="scratch")
    ap.add_argument("--only", choices=["s1", "s1b", "probe"])
    ap.add_argument("--keep", action="store_true", help="leave the spike workflow in the slot")
    ap.add_argument("--no-hop", action="store_true", help="skip S1's 12-hop turn (25 code runs)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    res = {"at": stamp, "now": NOW}
    try:
        if a.only == "probe":
            print("== probe")
            s = save(a.slot, workflow(), "probe")
            res["probe"], _ = run_probe(s["workflow_id"])
        if a.only in (None, "s1"):
            print("== S1")
            s = save(a.slot, workflow(), "s1")
            res["s1"], call = run_s1(s["workflow_id"], hop=not a.no_hop)
            res["s1"]["version"] = s.get("version")
            (OUT / f"s1-{stamp}-call.json").write_text(json.dumps(call, indent=1), encoding="utf-8", newline="\n")
        if a.only in (None, "s1b"):
            print("== S1b (brain padded to 60 KB)")
            wf = workflow(pad_kb=60)
            res["s1b_brain_bytes"] = len(wf["nodes"]["brain"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"])
            s = save(a.slot, wf, "s1b")
            res["s1b"], call = run_s1b(s["workflow_id"], res.get("s1"))
            res["s1b"]["version"] = s.get("version")
            (OUT / f"s1b-{stamp}-call.json").write_text(json.dumps(call, indent=1), encoding="utf-8", newline="\n")
    finally:
        if not a.keep:
            base = json.loads((ROOT / "examples" / "base-agent.json").read_text(encoding="utf-8"))
            s = freya.save_workflow(a.slot, base)
            print(f"restored the base agent in slot {a.slot}: version {s.get('version')}")
        path = OUT / f"{a.only or 's1'}-{stamp}.json"
        path.write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
        print("results:", path.relative_to(ROOT))
    checks = [c for k in ("probe", "s1", "s1b") for c in (res.get(k) or {}).get("checks", [])]
    print("\n" + ", ".join(f"{sum(c['status'] == s for c in checks)} {s}" for s in ("PASS", "FAIL", "INFO", "INVALID")))


if __name__ == "__main__":
    main()


