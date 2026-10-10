"""Spikes S2, S2b, S3 (item 04, throwaway): engine-run bank calls and the lifecycle close on the chat API.

    python3 spikes/s2_api.py [--slot scratch] [--only s2|s2b|s3] [--keep]

One spike workflow: a code brain that routes to api_request nodes whose names differ from their functions
(n_verify -> verify_identity, n_account -> get_account, n_promise -> create_promise, n_timeout -> get_account
with a 1 ms timeout), plus a workflow-level close_call lifecycle tool with baked {{final_outcome}} and
{{final_reason}} and a pre-hook that drops the sentinel reason "-". Each scenario opens a fresh practice
session (S2: PUB-01, S2b: PUB-10 with its create_promise fault, S3: PUB-02) and drives the call through
POST /api/v2/chat/message with commands. The bank's ledger is the ground truth for what was sent.
At the end the slot gets the base agent back (skip with --keep). Results go to spikes/results/.
"""
import argparse, json, pathlib, re, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT / "spikes"))
import freya
from score import http
from s1_loop import always, when, code_node, reply_to, check

OUT = ROOT / "spikes" / "results"
API = json.loads((ROOT / "tools" / "openapi.json").read_text(encoding="utf-8"))
BASE = API["servers"][0]["url"].rstrip("/")
TOOLS = ("verify_identity", "get_account", "create_promise", "close_call")

# ---------------------------------------------------------------- workflow

BRAIN = r"""
import json, re
NODES = dict(verify=['n_verify', 'verify_identity'], account=['n_account', 'get_account'],
             promise=['n_promise', 'create_promise'], timeout=['n_timeout', 'get_account'])
MAPPED = dict(verify=['vr_ok', 'vr_verified', 'vr_all', 'vr_left', 'vr_error', 'vr_hook'],
              account=['acct_ok', 'acct_products', 'acct_error'],
              promise=['pr_ok', 'pr_id', 'pr_error'], timeout=['to_ok', 'to_products', 'to_error'])
RESET = dict(verify=True, account=True, promise=True, timeout=False)


def counts(names):
    out = dict()
    for n in names:
        for suf in ('_success_count', '_failure_count', '_status_code'):
            if vars.get(n + suf) is not None:
                out[n + suf] = vars.get(n + suf)
    return out


def seen(prefixes):
    out = dict()
    for k in sorted(vars.keys()):
        if any(k.startswith(p) for p in prefixes):
            v = vars.get(k)
            out[k] = type(v).__name__ + ':' + repr(v)[:100]
    return out


def flat_products(node):
    prods = []
    for i in range(10):
        pid = vars.get(node + '_response.data.products.' + str(i) + '.product_id')
        if pid is None:
            break
        prods.append([str(pid), vars.get(node + '_response.data.products.' + str(i) + '.past_due')])
    return prods


def as_list(v):
    if isinstance(v, list):
        return v, 'list'
    if isinstance(v, str) and v.strip().startswith('['):
        try:
            return json.loads(v), 'json-string'
        except Exception:
            try:
                import ast
                return ast.literal_eval(v), 'python-repr-string'
            except Exception:
                return [], 'unparsed-string'
    return [], type(v).__name__


try:
    raw = vars.get('state')
    st = json.loads(raw) if isinstance(raw, str) and raw else dict()
    st['runs'] = st.get('runs', 0) + 1
    route, say = 'talk', ''
    pend = st.pop('pending', '')
    if pend:
        n = st.get('n_' + pend, 0) + 1
        st['n_' + pend] = n
        before = st.pop('snap', dict())
        after = counts(NODES[pend])
        mapped = dict()
        for k in MAPPED[pend]:
            v = vars.get(k)
            mapped[k] = type(v).__name__ + ':' + repr(v)[:160]
        dbg = dict(before=before, after=after, mapped=mapped, keys=seen(NODES[pend] + MAPPED[pend] + ['hook_']))
        if pend == 'account':
            lst, form = as_list(vars.get('acct_products'))
            dbg['products_form'] = form
            dbg['products_mapped'] = [[str(p.get('product_id')), p.get('past_due')] for p in lst if isinstance(p, dict)]
            prods = flat_products('n_account')
            st['products'] = prods
            dbg['products'] = prods
            dbg['past_due_type'] = type(prods[0][1]).__name__ if prods else 'none'
        vars['dbg_' + pend + '_' + str(n)] = json.dumps(dbg)
        short = ', '.join(k + '=' + str(vars.get(k))[:40] for k in MAPPED[pend] if not k.endswith('products'))
        say = pend.capitalize() + ' ' + str(n) + ' back. ' + short + '. Counts ' + json.dumps(after) + '.'
    else:
        said = str(vars.get('said') or '').strip()
        low = said.lower()
        st['turn'] = st.get('turn', 0) + 1
        m_ver = re.search(r'verify\s+([a-z_0-9]+)\s+(\S+)', low)
        m_fin = re.search(r'final\s+([a-z_]+)(?:\s+(\S+))?', low)
        m_date = re.search(r'(\d{4}-\d{2}-\d{2})', low)
        nxt = ''
        if m_ver:
            vars['v_field'] = m_ver.group(1)
            vars['v_answer'] = m_ver.group(2).rstrip('.')
            nxt = 'verify'
        elif m_fin:
            vars['final_outcome'] = m_fin.group(1)
            if m_fin.group(2):
                vars['final_reason'] = m_fin.group(2).rstrip('.')
            say = 'Final set: ' + str(vars.get('final_outcome')) + ' / ' + str(vars.get('final_reason')) + '.'
        elif 'timeout' in low:
            nxt = 'timeout'
        elif 'account' in low:
            nxt = 'account'
        elif 'promise' in low:
            prods = st.get('products') or []
            if prods:
                vars['v_product'] = prods[0][0]
                vars['v_amount'] = float(prods[0][1])
                vars['v_date'] = m_date.group(1) if m_date else str(vars.get('now') or '')[:10]
                nxt = 'promise'
            else:
                say = 'No products yet.'
        elif 'bye' in low:
            route = 'end_ok'
        else:
            say = 'Unknown command, turn ' + str(st['turn']) + '.'
        if nxt:
            if RESET[nxt]:
                for k in MAPPED[nxt]:
                    vars[k] = ''
            st['pending'] = nxt
            st['snap'] = counts(NODES[nxt])
            route = 'n_' + nxt
    vars['route'] = route
    vars['say_next'] = say
    vars['state'] = json.dumps(st)
except Exception as e:
    vars['route'] = 'error'
    vars['say_next'] = 'Brain error ' + repr(e)[:120]
"""

HOOK_PRE_VERIFY = """
try:
    import json
    vars['hook_pre_verify'] = str(int(vars.get('hook_pre_verify') or 0) + 1)
    b = config.get('body') if isinstance(config, dict) else None
    vars['hook_pre_body'] = json.dumps(b)[:300] if b is not None else 'none'
except Exception:
    pass
"""

HOOK_POST_VERIFY = """
try:
    vars['hook_post_verify'] = str(int(vars.get('hook_post_verify') or 0) + 1)
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            d['hook_mark'] = 'post-ran'
except Exception:
    pass
"""

# Rebuilds outcome and reason from the call variables, so a model fill of an unresolved placeholder never
# reaches the bank (S3 run 1 saw one). Unset final_outcome -> the default pair; reason "-" or unset -> no reason.
HOOK_CLOSE = """
try:
    b = config.get('body')
    if isinstance(b, dict):
        o = str(vars.get('final_outcome') or '').strip()
        r = str(vars.get('final_reason') or '').strip()
        if not o:
            o, r = 'not_reached', 'unclear'
        b['outcome'] = o
        if r and r != '-':
            b['reason'] = r
        else:
            b.pop('reason', None)
except Exception:
    pass
"""


def api_tool(fn, body, timeout_ms=10000):
    props = {"url": {"type": "string", "default": f"{BASE}/{fn}"}, "method": {"type": "string", "default": "POST"},
             "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
             "body": {"type": "object", "properties": body, "required": list(body)}}
    return {"type": "function", "config": {"timeoutMs": timeout_ms, "maxRetries": 0},
            "function": {"name": fn, "handler": "__function__:api_call", "description": f"Spike call of {fn}.",
                         "parameters": {"type": "object", "properties": props}}}


def api_node(name, tool, mapping):
    return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "brain",
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [tool],
            "response_mapping": [{"path": p, "variable": v} for p, v in mapping], "transitions": [always("brain")]}


def s(default, typ="string"):
    return {"type": typ, "default": default}


CID = s("{{customer_id}}")


def talk_node(name, text):
    return {"name": name, "node_type": "conversation", "on_enter": {"type": "tts", "text": text},
            "task_message": "Say exactly what your instructions give you. No tool call is allowed.",
            "extraction_schema": [{"name": "said", "type": "string", "description": "The customer's LAST message, copied verbatim.",
                                   "min_confidence": 0.1}],
            "max_node_turns": 200, "disable_inactivity_monitor": True, "transitions": [always("brain")]}


def workflow():
    verify = api_tool("verify_identity", {"customer_id": CID, "field": s("{{v_field}}"), "answer": s("{{v_answer}}")})
    verify["pre_process_code"], verify["post_process_code"] = HOOK_PRE_VERIFY, HOOK_POST_VERIFY
    promise = api_tool("create_promise", {"customer_id": CID, "product_id": s("{{v_product}}"),
                                          "amount": s("{{v_amount}}", "number"), "date": s("{{v_date}}")})
    close = api_tool("close_call", {"customer_id": CID, "outcome": s("{{final_outcome}}"), "reason": s("{{final_reason}}")}, 12000)
    close["function"]["description"] = "Record how the call ended."
    close["function"]["parameters"]["properties"]["body"]["required"] = ["customer_id", "outcome"]
    body = close["function"]["parameters"]["properties"]["body"]["properties"]
    body["outcome"]["description"] = "How the call ended."
    body["reason"]["description"] = "Why the call ended that way."
    close["config"]["alwaysRunsAt"] = ["end_of_call"]
    close["pre_process_code"] = HOOK_CLOSE
    nodes = {
        "init": code_node("init", "vars['init_ran'] = 'yes'\n", [always("opening")]),
        "opening": talk_node("opening", "S2 opening. Send a command."),
        "brain": code_node("brain", BRAIN, [
            when("brain_response.status", "equals", "error", "talk_error"),
            when("route", "equals", "n_verify", "n_verify"),
            when("route", "equals", "n_account", "n_account"),
            when("route", "equals", "n_promise", "n_promise"),
            when("route", "equals", "n_timeout", "n_timeout"),
            when("route", "equals", "end_ok", "end_ok"),
            when("route", "equals", "talk", "talk"),
            always("talk_fallback")]),
        "talk": talk_node("talk", "{{say_next}}"),
        "talk_fallback": talk_node("talk_fallback", "Fallback. Route was {{route}}."),
        "talk_error": talk_node("talk_error", "The brain run failed."),
        "n_verify": api_node("n_verify", verify, [("data.ok", "vr_ok"), ("data.verified", "vr_verified"), ("data.all_verified", "vr_all"),
                                                  ("data.wrong_answers_left", "vr_left"), ("data.error", "vr_error"), ("data.hook_mark", "vr_hook")]),
        "n_account": api_node("n_account", api_tool("get_account", {"customer_id": CID}),
                              [("data.ok", "acct_ok"), ("data.products", "acct_products"), ("data.error", "acct_error")]),
        "n_promise": api_node("n_promise", promise, [("data.ok", "pr_ok"), ("data.promise_id", "pr_id"), ("data.error", "pr_error")]),
        "n_timeout": api_node("n_timeout", api_tool("get_account", {"customer_id": CID}, timeout_ms=1),
                              [("data.ok", "to_ok"), ("data.products", "to_products"), ("data.error", "to_error")]),
        "end_ok": {"name": "end_ok", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                   "transitions": [], "terminal": {"type": "function", "function": {
                       "name": "spike_end", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {
                           "message_mode": {"type": "string", "default": "custom"},
                           "spoken_message": {"type": "string", "default": "S2 farewell."}}}}}},
    }
    return {"initial_node": "init", "system_prompt": "You are a test harness. Say only what your instructions give you, word for word.",
            "nodes": nodes, "global_nodes": {}, "tools": [close]}


# ---------------------------------------------------------------- chat driver and readers

def chat(wid, cv, turns):
    """Send the turns one by one; do not end the call. A 504 is waited out by polling the call's messages."""
    cid, rows = None, []
    for n, msg in enumerate(turns, 1):
        body = {"workflowId": wid, "message": msg}
        body.update({"callId": cid} if cid else {"customerVariables": cv})
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
        print(f"  {r['s']:6.2f}s  > {msg}\n          < {r.get('agent')}" + (f"   [{r['error'][:60]}]" if r.get("error") else ""), flush=True)
        if r.get("error") and r.get("agent") is None:
            break
    return cid, rows


def end_chat(cid):
    try:
        freya.api("POST", "/api/v2/chat/end", {"callId": cid})
        return "ok"
    except Exception as e:
        return str(e)[:200]


def ledger(customer_id):
    return http("/v1/practice/ledger/" + customer_id)


def wait_close(customer_id, seconds, at_least=1):
    """Poll the ledger until it holds `at_least` close_call entries or the time is up."""
    t, led = time.time(), {}
    while time.time() - t < seconds:
        led = ledger(customer_id)
        if sum(c["tool"] == "close_call" for c in led.get("calls", [])) >= at_least:
            break
        time.sleep(5)
    return led, round(time.time() - t, 1)


def val(v):
    return v.get("value") if isinstance(v, dict) and "value" in v else v


def final_vars(call):
    """The last variable table in the call's messages, values unwrapped."""
    last = {}
    for m in call.get("messages") or []:
        snap = (m.get("workflowSnapshot") or {}).get("variableTable")
        if isinstance(snap, dict):
            last = snap
        if m.get("role") == "system":
            try:
                ev = json.loads(m.get("message") or "")
            except Exception:
                continue
            if isinstance(ev.get("variable_table"), dict):
                last = ev["variable_table"]
    return {k: val(v) for k, v in last.items()}


def api_events(call):
    """Function events of the bank tools, in order. A pre_actions call is logged as function `api_call`; its tool
    is taken from the url of its start event, and the complete/error event that follows inherits it."""
    out, cur = [], None
    for m in call.get("messages") or []:
        if m.get("role") != "system":
            continue
        try:
            ev = json.loads(m.get("message") or "")
        except Exception:
            continue
        fn, kind = ev.get("function"), str(ev.get("event", ""))
        if not kind.startswith("function_call") or fn in ("code_exec", "end_call"):
            continue
        if fn == "api_call" and kind == "function_call_start":
            cur = str((ev.get("args") or {}).get("url", "")).rstrip("/").rsplit("/", 1)[-1]
        tool = cur if fn == "api_call" else fn
        if tool in TOOLS:
            out.append({"event": kind, "function": tool, "args": ev.get("args"), "result": ev.get("result"),
                        "error": ev.get("error"), "t": m.get("secondsFromStart")})
    return out


def keys_like(vt, prefixes):
    return {k: vt[k] for k in sorted(vt) if any(k.startswith(p) for p in prefixes)}


def dbg(vt, name):
    try:
        return json.loads(vt.get(name) or "null")
    except Exception:
        return vt.get(name)


def show_ledger(led):
    print(f"  ledger: outcome {led.get('outcome')!r}, reason {led.get('reason')!r}, promises {led.get('promises')}, failures {led.get('failures')}")
    for c in led.get("calls", []):
        print(f"    #{c['n']} {c['tool']:<16} {c.get('status')}  args {json.dumps(c.get('args'))}  ->  {json.dumps(c.get('result'))[:200]}")


def session(case):
    return http("/v1/practice/sessions", {"case": case})["variables"]


def run(wid, case, script, hang_up=False, close_wait=60):
    """Open a practice session, drive the chat, then read the ledger. With hang_up the call is ended through
    POST /api/v2/chat/end right after the script; otherwise the script ends at the end node, the ledger is read,
    and only then chat/end is sent, to see whether it closes a second time."""
    cv = session(case)
    print(f"  session {case}: customer_id {cv['customer_id']}, now {cv.get('now')}")
    cid, rows = chat(wid, cv, script)
    res = {"case": case, "customer_id": cv["customer_id"], "variables": cv, "call_id": cid, "rows": rows}
    if hang_up:
        res["chat_end"] = end_chat(cid)
        led, waited = wait_close(cv["customer_id"], close_wait)
        res["close_after_hangup_s"] = waited
    else:
        led, waited = wait_close(cv["customer_id"], close_wait)
        res["close_after_end_node_s"] = waited
        res["closes_before_chat_end"] = sum(c["tool"] == "close_call" for c in led.get("calls", []))
        res["chat_end"] = end_chat(cid)
        led, _ = wait_close(cv["customer_id"], 15, at_least=res["closes_before_chat_end"] + 1)
    res["ledger"] = led
    call = freya.get_call(cid)
    res["ended_reason"] = call.get("endedReason")
    res["api_events"] = api_events(call)
    res["vars"] = final_vars(call)
    show_ledger(led)
    return res, call


def closes(led):
    return [c for c in led.get("calls", []) if c["tool"] == "close_call"]


def tool_calls(led, tool):
    return [c for c in led.get("calls", []) if c["tool"] == tool]


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ---------------------------------------------------------------- scenarios

def run_s2(wid):
    script = ["Hello?", "verify date_of_birth 1988-03-14", "verify phone_last4 4471", "account", "promise", "final promise -", "bye"]
    res, call = run(wid, "PUB-01", script)
    led, vt, out = res["ledger"], res["vars"], []
    ver = tool_calls(led, "verify_identity")
    pro = tool_calls(led, "create_promise")
    out.append(check("D19 api bodies baked from code vars (verify)",
                     [c["args"] for c in ver] == [{"field": "date_of_birth", "answer": "1988-03-14"}, {"field": "phone_last4", "answer": "4471"}]
                     and all(c["result"].get("verified") for c in ver),
                     f"{[c['args'] for c in ver]} -> {[c['result'] for c in ver]}"))
    pa = pro[0]["args"] if pro else {}
    out.append(check("D19 create_promise body from code vars (product id from the mapped list, date from now)",
                     pa.get("product_id") == "p1" and pa.get("date") == res["variables"]["now"][:10], json.dumps(pa)))
    sent = [(e["args"] or {}).get("body") for e in res["api_events"] if e["function"] == "create_promise" and e["event"] == "function_call_start"]
    out.append(check("D20 amount arrives as a JSON number", is_num(pa.get("amount")),
                     f"ledger amount {pa.get('amount')!r} ({type(pa.get('amount')).__name__}); Freya sent {sent}"))
    names = ["n_verify", "verify_identity", "n_account", "get_account", "n_promise", "create_promise"]
    naming = {p: sorted(k for k in vt if k.startswith(p + "_")) for p in names}
    out.append(check("D22 automatic key naming, node != function", None,
                     "; ".join(f"{p}: {len(ks)} keys, e.g. {[k for k in ks if not k.startswith(p + '_response.header')][:6]}" for p, ks in naming.items())))
    acc = dbg(vt, "dbg_account_1") or {}
    out.append(check("Mapped product list (data.products -> acct_products)", None,
                     f"in code: {acc.get('products_form')} {acc.get('products_mapped')}; in the variable table: "
                     f"{type(vt.get('acct_products')).__name__} {repr(vt.get('acct_products'))[:160]}"))
    out.append(check("Product list read from flattened keys <node>_response.data.products.<i>.<field>", bool(acc.get("products")),
                     f"{acc.get('products')}, past_due type {acc.get('past_due_type')}"))
    v1 = dbg(vt, "dbg_verify_1") or {}
    out.append(check("Hooks on a pre_actions api node (pre and post)", None,
                     f"hook_pre_verify={vt.get('hook_pre_verify')!r}, hook_post_verify={vt.get('hook_post_verify')!r}, "
                     f"vr_hook (mapped data.hook_mark)={vt.get('vr_hook')!r}, pre-hook saw body {vt.get('hook_pre_body')!r}; "
                     f"counts after verify 1: {v1.get('after')}"))
    cl = closes(led)
    out.append(check("Lifecycle close_call fires after the end node (chat route)", res["closes_before_chat_end"] >= 1,
                     f"{res['closes_before_chat_end']} close_call(s) {res['close_after_end_node_s']} s after the farewell, before chat/end"))
    out.append(check("D30 pre-hook on the lifecycle tool drops the sentinel reason", bool(cl) and "reason" not in cl[0]["args"] and cl[0]["result"].get("ok") is True,
                     f"close args {[c['args'] for c in cl]} -> {[c['result'] for c in cl]}"))
    out.append(check("No second close_call after chat/end", len(cl) == 1, f"{len(cl)} close_call(s) after chat/end ({res['chat_end']})"))
    out.append(check("ACCEPTANCE ledger: exactly 1 promise, numeric amount, reason null, outcome promise",
                     len(led.get("promises", [])) == 1 and is_num(led["promises"][0].get("amount")) and led.get("reason") is None
                     and led.get("outcome") == "promise",
                     f"promises {led.get('promises')}, outcome {led.get('outcome')!r}, reason {led.get('reason')!r}"))
    res["checks"] = out
    return res, call


def run_s2b(wid):
    script = ["Hello?", "verify date_of_birth 1989-02-23", "verify phone_last4 9904", "account",
              "promise 2026-10-16", "promise 2026-10-16", "timeout", "final promise -", "bye"]
    res, call = run(wid, "PUB-10", script)
    led, vt, out = res["ledger"], res["vars"], []
    pro = tool_calls(led, "create_promise")
    p1, p2 = dbg(vt, "dbg_promise_1") or {}, dbg(vt, "dbg_promise_2") or {}
    ev = [e for e in res["api_events"] if e["function"] == "create_promise"]
    out.append(check("D23 500 envelope on create_promise (Freya side)", None,
                     f"ledger status {[c.get('status') for c in pro]}; events {[(e['event'], e['result'] or e['error']) for e in ev if e['event'] != 'function_call_start']}"))
    fail_seen = (p1.get("after") != p1.get("before")) or "False" in str(p1.get("mapped")) or "false" in str(p1.get("mapped"))
    out.append(check("D23 the failure is visible to code (counters or mapped ok)", fail_seen,
                     f"before {p1.get('before')} after {p1.get('after')}; mapped {p1.get('mapped')}"))
    out.append(check("D23 the retry succeeds and is visible", "True" in str(p2.get("mapped", {}).get("pr_ok")) or "true" in str(p2.get("mapped", {}).get("pr_ok")),
                     f"before {p2.get('before')} after {p2.get('after')}; mapped {p2.get('mapped')}"))
    stale = {k: v for k, v in (p2.get("keys") or {}).items() if "error" in k}
    out.append(check("D23 stale keys after the successful retry", None, f"error-like keys seen by code after retry: {stale}"))
    to = dbg(vt, "dbg_timeout_1") or {}
    out.append(check("Transport failure (1 ms timeout on get_account): envelope, counters, stale keys", None,
                     f"before {to.get('before')} after {to.get('after')}; mapped {to.get('mapped')}; "
                     f"get_account keys {{{', '.join(f'{k}: {v}' for k, v in (to.get('keys') or {}).items() if 'products' in k or 'status' in k or 'ok' in k)}}}; "
                     f"events {[(e['event'], str(e['result'] or e['error'])[:160]) for e in res['api_events'] if e['function'] == 'get_account' and e['event'] != 'function_call_start'][-1:]}"))
    out.append(check("ACCEPTANCE S2b ledger: exactly 1 promise after the retry, outcome promise, reason null",
                     len(led.get("promises", [])) == 1 and led.get("outcome") == "promise" and led.get("reason") is None
                     and is_num(led["promises"][0].get("amount")),
                     f"promises {led.get('promises')}, create_promise calls {len(pro)}, outcome {led.get('outcome')!r}, reason {led.get('reason')!r}"))
    res["checks"] = out
    return res, call


def run_s3(wid):
    out, res = [], {}
    print("-- S3a: reason never set, end at the end node")
    a, call_a = run(wid, "PUB-02", ["Hello?", "final not_reached", "bye"])
    cl = closes(a["ledger"])
    filled = [(e["args"] or {}).get("body") for e in a["api_events"] if e["function"] == "close_call" and e["event"] == "function_call_start"]
    out.append(check("D28 unresolved {{final_reason}}: the close pre-hook overrides the model fill", bool(cl) and "reason" not in cl[0]["args"],
                     f"Freya start event body {filled}; bank got {[c['args'] for c in cl]} -> {[c['result'] for c in cl]}"))
    print("-- S3b: hang-up (chat/end) after the brain set final_*")
    b, call_b = run(wid, "PUB-02", ["Hello?", "final not_reached unclear"], hang_up=True)
    cl = closes(b["ledger"])
    out.append(check("Close on hang-up with final_* set", bool(cl) and cl[0]["args"].get("outcome") == "not_reached" and cl[0]["args"].get("reason") == "unclear",
                     f"chat/end {b['chat_end']}; close args {[c['args'] for c in cl]} -> {[c['result'] for c in cl]} after {b['close_after_hangup_s']} s; endedReason {b['ended_reason']}"))
    print("-- S3c: hang-up right after the opening, nothing set")
    c, call_c = run(wid, "PUB-02", ["Hello?"], hang_up=True)
    cl = closes(c["ledger"])
    out.append(check("Close on hang-up right after the opening (final_* unset): hook default pair",
                     bool(cl) and cl[0]["args"].get("outcome") == "not_reached" and cl[0]["args"].get("reason") == "unclear",
                     f"chat/end {c['chat_end']}; close args {[x['args'] for x in cl]} -> {[x['result'] for x in cl]} after {c['close_after_hangup_s']} s; "
                     f"ledger outcome {c['ledger'].get('outcome')!r} reason {c['ledger'].get('reason')!r}; endedReason {c['ended_reason']}"))
    res = {"s3a": a, "s3b": b, "s3c": c, "checks": out}
    return res, {"s3a": call_a, "s3b": call_b, "s3c": call_c}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="scratch")
    ap.add_argument("--only", choices=["s2", "s2b", "s3"])
    ap.add_argument("--keep", action="store_true", help="leave the spike workflow in the slot")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    res = {"at": stamp}
    try:
        wf = workflow()
        print("dry run:", freya.validate_workflow("spike s2", wf))
        st = freya.save_workflow(a.slot, wf)
        print(f"saved to slot {a.slot}: workflow {st['workflow_id']} version {st.get('version')}")
        res["version"] = st.get("version")
        for name, fn in (("s2", run_s2), ("s2b", run_s2b), ("s3", run_s3)):
            if a.only not in (None, name):
                continue
            print(f"== {name.upper()}")
            try:
                res[name], call = fn(st["workflow_id"])
            except Exception as e:
                print(f"  {name} aborted: {e!r}")
                res[name] = {"error": repr(e)[:500]}
                continue
            (OUT / f"{name}-{stamp}-call.json").write_text(json.dumps(call, indent=1), encoding="utf-8", newline="\n")
    finally:
        if not a.keep:
            base = json.loads((ROOT / "examples" / "base-agent.json").read_text(encoding="utf-8"))
            s = freya.save_workflow(a.slot, base)
            print(f"restored the base agent in slot {a.slot}: version {s.get('version')}")
        path = OUT / f"s2-{stamp}.json"
        path.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8", newline="\n")
        print("results:", path.relative_to(ROOT))
    checks = [c for k in ("s2", "s2b", "s3") for c in (res.get(k) or {}).get("checks", [])]
    print("\n" + ", ".join(f"{sum(c['status'] == x for c in checks)} {x}" for x in ("PASS", "FAIL", "INFO", "INVALID")))


if __name__ == "__main__":
    main()
