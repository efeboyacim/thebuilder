"""The brain's policy engine: one generic state machine, parameterised by the compact spec (builder/brain.py).

One run of the brain is one call of `run(vars, S, E)`:
- S: the compact spec; E: {expression path: lambda n, h: ...} (n: the expression's names, h: policy_lib.helpers).
- After a talk node it reads that node's extraction fields (x_*), clears them, and handles exactly one customer turn.
- After an api node it reads the node's success/failure counters and its mapped response variables (r_<role>__*),
  which the brain reset to "" before routing there, and the flattened product list keys.
- It writes `route` (a talk node, an api node or the end node), `say_next`, `farewell`, the api arguments
  (a_<role>__<field>), `final_outcome`/`final_reason` (the situation that fits a hang-up right now), `say_fallback`
  and its state (`brain_state`, one JSON string). It never raises: on an error it writes `brain_error` and a route
  no transition matches, so the brain's last `always` takes the call to talk_fallback.

Same rules as policy_lib.py (it is inlined into the same brain). Nothing here knows a brief.
"""
import datetime, json, re
from builder.policy_lib import *  # brain: strip

NS = "not_specified"
TALK, API, END, FAILED = "talk_", "api_", "end_call", "brain_failed"
STATE = "brain_state"
STAGES = ("open", "third", "identity", "negotiate", "confirm")
SLOTS = ("main", "side_q", "event", "reply", "answer", "day", "time", "amount", "said")
SIDE_Q = ("purpose", "other")
CONTINUE = "Please go on."
LOOP_LIMIT = 3
TURN_LIMIT = 40
LLM = "_llm"


# ---------- names shared with the compiler ----------

def talk_node(stage):
    return TALK + stage


def api_node(role):
    return API + role


def arg_var(role, path):
    return "a_" + role + "__" + re.sub(r"[^A-Za-z0-9]+", "_", path)


def resp_var(role, r):
    return "r_" + role + "__" + r


def x_var(slot):
    return "x_" + slot


def llm_node(stage):
    return TALK + stage + LLM


def side_ids(S):
    return [a.get("id") for a in (S.get("side_questions") or dict()).get("answers") or []] + list(SIDE_Q)


def llm_answers(S):
    return ((S.get("side_questions") or dict()).get("other") or dict()).get("policy") == "answer_from_facts"


# ---------- entry ----------

def run(vars, S, E):
    try:
        c = context(vars, S, E)
        step(c)
        flush(c)
    except Exception as e:  # never raise inside the code node
        vars["brain_error"] = (type(e).__name__ + ": " + str(e))[:400]
        vars["route"] = FAILED
    return vars


def context(vars, S, E):
    b = S["bindings"]
    now = parse_now(vars.get(b["call_vars"].get("clock")))
    if now is None:  # the clock variable is missing or unreadable: the only use of the real clock
        now = datetime.datetime.now().replace(second=0, microsecond=0)
    cal = calendar(S["calendar"].get("business_weekdays"), S["calendar"].get("holidays"))
    try:
        st = json.loads(vars.get(STATE) or "")
    except ValueError:
        st = None
    if not isinstance(st, dict):
        st = new_state()
    cv = dict((name, vars.get(name)) for name in S.get("call_variables") or [])
    wmode = "calendar_week" if S["calendar"].get("weekday_semantics") == "calendar_week" else "nearest"
    return dict(v=vars, S=S, E=E, now=now, today=now.date(), cal=cal, H=helpers(cal), st=st, say=[], out=dict(),
                cv=cv, wmode=wmode)


def new_state():
    return dict(stage="open", wait="open", asks=1, miss=0, last="", loops=0, closing="", transferred=False,
                pending=None, third=dict(bad=0, none=0, other=0, day="", cb=None),
                ident=dict(q=0, wrong=0, qwrong=dict(), partial=None, refusals=0, purpose=0, fails=0, passed=[]),
                products=[], offers=[], agreed=[], recorded=[], fails=dict(), pp=dict(), cur="", conf=0,
                no_transfer=False, turns=0, ev=dict(), pev=None, sup=[], sup_no="", hang="", resume="", facts="")


def step(c):
    st = c["st"]
    p = st.get("pending")
    if p:
        st["pending"] = None
        return on_result(c, p, api_result(c, p))
    stage = st.get("wait") or st.get("stage") or "open"
    st["wait"] = ""
    return on_turn(c, stage, read_turn(c))


def flush(c):
    S, st, out = c["S"], c["st"], c["out"]
    sid = final_situation(c)
    sit = situation(c, sid) or dict(outcome="", reason="")
    out["final_outcome"] = sit.get("outcome") or ""
    out["final_reason"] = sit.get("reason") or "-"
    out["say_fallback"] = st.get("last") or render(S["opening"]["confirmation"]["reask_template"], values(c))
    for slot in SLOTS:
        out[x_var(slot)] = ""
    out["facts"] = facts_text(c)
    out["brain_stage"] = st.get("stage") or ""
    out["brain_error"] = ""
    out[STATE] = json.dumps(st, separators=(",", ":"))
    for k in out:  # item by item: the platform may only see assignments
        c["v"][k] = out[k]


# ---------- speaking, closing, calling ----------

def ask(c, stage, text):
    st = c["st"]
    text = render(text, values(c))
    st["stage"], st["wait"], st["last"], st["evq"] = stage, stage, text, False
    c["out"]["say_next"] = join(*[render(s, values(c)) for s in c["say"] + [text]])
    c["say"] = []
    c["out"]["route"] = talk_node(stage)
    if c.get("llm"):
        c["out"]["route"] = llm_node(stage)
        c["out"]["side_question"] = c["llm"]
        c["llm"] = ""


def situation(c, sid):
    return next((s for s in c["S"]["situations"] if s.get("id") == sid), None)


def hangup(c, stage):
    h = c["S"]["closing"]["hangup"].get(stage if stage in STAGES else "open") or dict()
    return h.get("situation") or ""


def sit_ref(c, ref, stage):
    """A situation reference from the spec; a missing or not_specified one is the stage's hang-up situation."""
    return ref if isinstance(ref, str) and situation(c, ref) is not None else hangup(c, stage)


def final_situation(c):
    S, st = c["S"], c["st"]
    on_rec = sit_ref(c, S["confirm"].get("on_recorded"), "confirm")
    if st.get("closing"):
        sit = situation(c, st["closing"]) or dict()
        if st.get("recorded") and sit.get("queue") and S["closing"].get("transfer_overrides_promise") is False:
            return on_rec
        return st["closing"]
    if st.get("recorded"):
        return on_rec
    if st.get("hang") and situation(c, st["hang"]) is not None:
        return st["hang"]
    return hangup(c, st.get("stage"))


def close(c, sid):
    S, st = c["S"], c["st"]
    sit = situation(c, sid)
    if sit is None:
        sid = hangup(c, st.get("stage"))
        sit = situation(c, sid) or dict(say="", farewell="")
    if sit.get("queue") and "transfer" in S["bindings"]["tools"] and not st.get("transferred") and             not st.get("no_transfer"):
        return call(c, "transfer", dict(queue=sit["queue"]), dict(kind="transfer", sid=sid))
    st["closing"] = sid
    vals = values(c)
    cb = st["third"].get("cb")
    if cb:
        vals["callback_date"] = say_date(datetime.date.fromisoformat(cb[0]), c["today"])
        vals["callback_time"] = cb[1]
    c["out"]["farewell"] = join(*[render(s, vals) for s in c["say"] + [sit.get("say"), sit.get("farewell")]])
    c["say"] = []
    c["out"]["route"] = END


def call(c, role, engine_values, ctx):
    S, st, v, out = c["S"], c["st"], c["v"], c["out"]
    b = S["bindings"]
    if role not in b["tools"]:
        raise ValueError("no tool bound to role " + role)
    roles = dict((r, v.get(name)) for r, name in b["call_vars"].items())
    roles.update(engine_values)
    for path, bind in (b["request"].get(role) or dict()).items():
        src, val = bind.get("source"), None
        if src == "literal":
            continue
        if src == "call_var":
            val = v.get(b["call_vars"].get(bind.get("value")))
        elif src == "template":
            val = render(bind.get("value"), roles)
        else:
            names = bind["value"] if isinstance(bind["value"], list) else [bind["value"]]
            args = [engine_values.get(n) for n in names]
            if all(a is not None for a in args):
                conv = bind.get("convert")
                val = CONVERT[conv](*args) if conv else args[0]
        if isinstance(val, (datetime.date, datetime.datetime)):
            val = val.isoformat()
        out[arg_var(role, path)] = "" if val is None else val
    node = api_node(role)
    for r in b["response"].get(role) or dict():
        if r not in ("item", "products"):
            out[resp_var(role, r)] = ""
    st["pending"] = dict(ctx, role=role, s=to_int(v.get(node + "_success_count")) or 0,
                         f=to_int(v.get(node + "_failure_count")) or 0)
    out["route"] = node


def api_result(c, p):
    S, v, role = c["S"], c["v"], p["role"]
    node = api_node(role)
    resp = S["bindings"]["response"].get(role) or dict()
    vals = dict((r, v.get(resp_var(role, r))) for r in resp if r not in ("item", "products"))
    s, f = to_int(v.get(node + "_success_count")) or 0, to_int(v.get(node + "_failure_count")) or 0
    ok = s == p["s"] + 1 and f == p["f"]
    if "ok" in resp:
        ok = ok and truthy(vals.get("ok"))
    return dict(ok=ok, values=vals)


def on_result(c, p, r):
    kind = p.get("kind")
    if kind == "verify":
        return verify_result(c, p, r)
    if kind == "lookup":
        return lookup_result(c, p, r)
    if kind == "promise":
        return promise_result(c, p, r)
    if kind == "callback":
        return callback_result(c, p, r)
    if kind == "transfer":
        if r["ok"]:
            c["st"]["transferred"] = True
            return close(c, p.get("sid"))
        return failed(c, "transfer", lambda: close(c, p.get("sid")), c["st"].get("stage"))
    raise ValueError("unknown pending call " + str(kind))


def failed(c, role, again, stage, key=None):
    """A failed bank call: call again while the spec's retries last, else close with its situation."""
    st = c["st"]
    key = key or role
    st["fails"][key] = st["fails"].get(key, 0) + 1
    pol = (c["S"].get("failure_policy") or dict()).get(role) or dict()
    n = to_int(pol.get("retries"))
    if st["fails"][key] <= (1 if n is None else n):
        return again()
    if role == "transfer":
        st["no_transfer"] = True
    return close(c, sit_ref(c, pol.get("on_exhausted"), stage))


# ---------- template values ----------

def values(c, extra=None):
    out = dict(c["cv"])
    out.update(extra or dict())
    return out


def product_name(c, p):
    for n in c["S"]["disclosure"].get("product_names") or []:
        if str(n.get("kind")) == str(p.get("kind")):
            return render(n.get("name_template"), values(c, p.get("raw")))
    return str(p.get("kind") or "").replace("_", " ")


def product_values(c, p, extra=None):
    vals = values(c, p.get("raw"))
    vals.update(dict(product_name=product_name(c, p), due_amount=say_money(p.get("due") or 0),
                     days_overdue=str(p.get("days") if p.get("days") is not None else "")))
    if p.get("deadline"):
        vals["deadline"] = say_date(datetime.date.fromisoformat(p["deadline"]), c["today"])
    vals.update(extra or dict())
    return vals


def expr_names(c, p=None):
    n = dict(today=c["today"], now=c["now"])
    if p is not None:
        n.update(dict((name, None) for name in c["S"]["_api"]["item_fields"]))
        n.update(p.get("raw") or dict())
        n.update(dict(ref=p.get("ref"), kind=p.get("kind"), due_amount=p.get("due"), days_overdue=p.get("days")))
    return n


def expr(c, path, names):
    f = c["E"].get(path)
    if f is None:
        raise ValueError("no expression " + path)
    return f(names, c["H"])


# ---------- one customer turn ----------

def read_turn(c):
    x = dict()
    for slot in SLOTS:
        val = c["v"].get(x_var(slot))
        x[slot] = "" if empty(val) else str(val).strip()
    for k in ("main", "side_q", "event"):
        x[k] = x[k].lower()
    return x


def fallback_main(c, stage, x):
    """Regex fallbacks on what the customer said when extraction gave nothing."""
    said = " " + x["said"].lower() + " "
    if stage == "open" and said.strip():
        family = str(c["v"].get(c["S"]["bindings"]["call_vars"].get("family_name")) or "").lower()
        yes = [w.lower() for w in c["S"]["opening"]["confirmation"].get("counts_as_yes") or [] if len(w.split()) <= 3]
        if any(re.search(r"\b" + re.escape(w) + r"\b", said) for w in yes) or (family and family in said):
            x["main"] = "confirmed"
            return True
    return False


def termination(c, stage, x):
    for t in c["S"]["third_party"].get("terminations") or []:
        if stage in (t.get("stages") or []) and t.get("id") in (x["main"], x["event"]):
            return t.get("situation")
    return ""


def on_turn(c, stage, x):
    st = c["st"]
    st["turns"] = st.get("turns", 0) + 1
    if st["turns"] > TURN_LIMIT:
        return close(c, hangup(c, stage))
    filled = any(x[k] for k in ("main", "side_q", "event", "reply", "answer", "day", "time", "amount"))
    if not filled and not fallback_main(c, stage, x):
        if not st.get("miss"):
            st["miss"] = 1
            return ask(c, stage, st.get("last") or "")
        x["main"] = "unclear"
    st["miss"] = 0
    t = termination(c, stage, x)
    if t:
        return close(c, t)
    if stage != "third":
        side_answer(c, stage, x)
    if st.get("pev") and event_reply(c, stage, x):
        return None
    if x["event"] and fire_event(c, stage, x):
        return None
    if stage == "open":
        return turn_open(c, x)
    if stage == "third":
        return turn_third(c, x)
    if stage == "identity":
        return turn_identity(c, x)
    if stage == "negotiate":
        return turn_negotiate(c, x)
    if stage == "confirm":
        return turn_confirm(c, x)
    raise ValueError("unknown stage " + str(stage))


# ---------- opening and third party ----------

def turn_open(c, x):
    S, st = c["S"], c["st"]
    conf = S["opening"]["confirmation"]
    if x["main"] == "confirmed":
        return start_identity(c)
    if x["main"] == "someone_else":
        return start_third(c, x)
    question = bool(x["side_q"])
    if not (question and conf.get("question_then_reask_counts") is False):
        if st["asks"] >= (to_int(conf.get("max_asks")) or 2):
            return close(c, sit_ref(c, conf.get("on_unclear"), "open"))
        st["asks"] += 1
    return ask(c, "open", conf["reask_template"])


def start_third(c, x):
    st = c["st"]
    st["stage"] = "third"
    if x["day"] or x["time"]:
        return turn_third(c, dict(x, main="gives_time"))
    tp = c["S"]["third_party"]
    if x["side_q"] or x["main"] == "offers_to_pay":
        return ask(c, "third", tp["deflect_template"])
    return ask(c, "third", tp["ask_reachable_template"])


def turn_third(c, x):
    S, st = c["S"], c["st"]
    tp, t3 = S["third_party"], st["third"]
    if x["main"] == "customer_available":
        st["asks"] = 1
        return ask(c, "open", S["opening"]["confirmation"]["reask_template"])
    if x["day"] or x["time"] or x["main"] == "gives_time":
        if tp["callback"].get("enabled") is not True:
            return close(c, sit_ref(c, tp.get("on_no_answer"), "third"))
        return callback_offer(c, x)
    if x["main"] == "no_time":
        t3["none"] += 1
        if t3["none"] > 1:
            return close(c, sit_ref(c, tp.get("on_no_answer"), "third"))
        return ask(c, "third", tp["ask_reachable_template"])
    t3["other"] += 1
    if t3["other"] > LOOP_LIMIT:
        return close(c, sit_ref(c, tp.get("on_no_answer"), "third"))
    return ask(c, "third", tp["deflect_template"])


def callback_offer(c, x):
    S, st, today = c["S"], c["st"], c["today"]
    cb, t3 = S["third_party"]["callback"], st["third"]
    d = customer_day(c, x["day"]) if x["day"] else None
    if d is None and t3.get("day"):
        d = datetime.date.fromisoformat(t3["day"])
    t = parse_time(x["time"]) if x["time"] else None
    if t is None:
        if d is not None:
            t3["day"] = d.isoformat()
            return ask(c, "third", cb["ask_time_template"])
        t3["none"] += 1
        if t3["none"] > 1:
            return close(c, sit_ref(c, S["third_party"].get("on_no_answer"), "third"))
        return ask(c, "third", S["third_party"]["ask_reachable_template"])
    if d is None:
        d = today
    for h, m in time_candidates(t):
        if callback_ok(c, d, h, m):
            t3["cb"] = [d.isoformat(), hhmm(h, m)]
            return call(c, "book_callback", dict(date=d, time=hhmm(h, m)), dict(kind="callback"))
    t3["bad"] += 1
    t3["day"] = ""
    retry = to_int(cb.get("retry_asks"))
    if t3["bad"] > (1 if retry is None else retry):
        return close(c, sit_ref(c, cb.get("on_invalid_exhausted"), "third"))
    return ask(c, "third", cb["invalid_template"])


def callback_ok(c, d, h, m):
    cb, now = c["S"]["third_party"]["callback"], c["now"]
    if "third_party.callback.day_rule" in c["E"] and \
            not expr(c, "third_party.callback.day_rule", dict(d=d, today=c["today"], now=now)):
        return False
    t = hhmm(h, m)
    wins = cb.get("windows")
    if isinstance(wins, list):
        si, ei = cb.get("start_inclusive") is not False, cb.get("end_inclusive") is not False
        if not any((w["start"] < t or (si and w["start"] == t)) and (t < w["end"] or (ei and w["end"] == t))
                   for w in wins):
            return False
    at = datetime.datetime(d.year, d.month, d.day, h, m)
    if cb.get("after_now") is not False and at <= now:
        return False
    lead = to_int(cb.get("min_lead_minutes"))
    if lead and at < now + datetime.timedelta(minutes=lead):
        return False
    g = to_int(cb.get("granularity_minutes"))
    if g and (h * 60 + m) % g:
        return False
    return True


def callback_result(c, p, r):
    if r["ok"]:
        return close(c, sit_ref(c, c["S"]["third_party"]["callback"].get("on_booked"), "third"))
    t3 = c["st"]["third"]
    cb, t3["cb"] = t3["cb"], None

    def again():
        t3["cb"] = cb
        return call(c, "book_callback", dict(date=datetime.date.fromisoformat(cb[0]), time=cb[1]), dict(kind="callback"))
    return failed(c, "book_callback", again, "third")


# ---------- side questions and events ----------

def side_answer(c, stage, x):
    """The answer to a side question goes before whatever the agent says next."""
    S, q = c["S"], x["side_q"]
    if not q or (q == "purpose" and stage in ("open", "identity")):
        return
    sq = S.get("side_questions") or dict()
    for a in sq.get("answers") or []:
        if a.get("id") == q:
            c["say"].append(a.get("template"))
            return
    if llm_answers(S):
        c["llm"] = x["said"] or q
    else:
        c["say"].append((sq.get("other") or dict()).get("no_info_template"))


def facts_text(c):
    st = c["st"]
    if not st.get("products"):
        return ""
    o = pstate(c, current(c)).get("offer") if st.get("cur") else None
    extra = ""
    if o:
        extra = " Offer on the table: " + say_money(o["amount"]) + " on " + \
                say_date(datetime.date.fromisoformat(o["date"]), c["today"]) + "."
    return (st.get("facts") or "") + extra


def event_row(c, eid):
    return next((e for e in c["S"].get("events") or [] if e.get("id") == eid), None)


def verified(c):
    return bool(c["st"].get("products"))


def target(c, t, stage):
    """An event's target: a situation ends the call, anything else goes back to what the agent was asking."""
    st = c["st"]
    if isinstance(t, str) and situation(c, t) is not None:
        return close(c, t)
    return ask(c, stage, st.get("resume") or st.get("last") or "")


def fire_event(c, stage, x):
    st = c["st"]
    e = event_row(c, x["event"])
    if e is None or stage not in (e.get("stages") or []) or e["id"] in st["sup"]:
        return False
    st["ev"][e["id"]] = st["ev"].get(e["id"], 0) + 1
    if st["ev"][e["id"]] < (to_int(e.get("min_occurrences")) or 1):
        if e.get("below_min_template") in (None, "", NS):
            return False
        if not st.get("evq"):
            st["resume"] = st.get("last") or ""
        st["pev"] = dict(id=e["id"], kind="below", n=0)
        ask(c, stage, e["below_min_template"])
        st["evq"] = True
        return True
    for k in e.get("suppresses") or []:
        if k not in st["sup"]:
            st["sup"].append(k)
    if "persuasion" in (e.get("suppresses") or []):
        st["sup_no"] = e["id"]
    if not st.get("evq"):
        st["resume"] = st.get("last") or ""
    if any(e.get(k) not in (None, "", NS) for k in ("on_yes", "on_no")):
        if situation(c, e.get("on_no")) is not None:
            st["hang"] = e["on_no"]
        st["pev"] = dict(id=e["id"], kind="question", n=0)
        ask(c, stage, e["template"])
        st["evq"] = True
        return True
    if e.get("template") not in (None, "", NS):
        c["say"].append(e["template"])
    t = e.get("then")
    if not verified(c) and e.get("then_before_verification") not in (None, "", NS):
        t = e["then_before_verification"]
    target(c, t, stage)
    return True


def event_reply(c, stage, x):
    """The customer's answer to an event's question (or to its question below min_occurrences)."""
    st = c["st"]
    pe, st["pev"] = st["pev"], None
    e = event_row(c, pe.get("id"))
    if e is None or x["day"] or x["amount"]:  # a date or an amount the customer names is handled as usual
        return False
    yes = x["reply"] == "yes" or x["main"] in ("accepts", "yes", "confirmed")
    no = x["reply"] == "no" or x["main"] in ("refuses", "no")
    if pe.get("kind") == "below":
        if no or x["event"] == e["id"]:
            return fire_event(c, stage, dict(x, event=e["id"]))
        if yes and not x["side_q"]:
            target(c, "continue", stage)
            return True
        return False
    if x["event"] and x["event"] != e["id"] and fire_event(c, stage, x):
        return True
    if yes:
        t = e.get("on_yes")
        if not verified(c) and e.get("then_before_verification") not in (None, "", NS):
            t = e["then_before_verification"]
        target(c, t, stage)
        return True
    if no:
        target(c, e.get("on_no"), stage)
        return True
    pe["n"] = pe.get("n", 0) + 1
    if pe["n"] > 1:
        target(c, e.get("on_no"), stage)
        return True
    st["pev"] = pe
    ask(c, stage, e["template"])
    st["evq"] = True
    return True


# ---------- identity ----------

def start_identity(c):
    c["st"]["stage"] = "identity"
    return ask_question(c, next_question(c, -1))


def next_question(c, after):
    qs = c["S"]["identity"]["questions"]
    passed = c["st"]["ident"]["passed"]
    for i in list(range(after + 1, len(qs))) + list(range(0, after + 1)):
        if qs[i]["field"] not in passed:
            return i
    return None


def ask_question(c, i, reask=False):
    q = c["S"]["identity"]["questions"][i]
    c["st"]["ident"]["q"] = i
    return ask(c, "identity", q["reask_template"] if reask else q["ask_template"])


def turn_identity(c, x):
    S, st = c["S"], c["st"]
    idt, s = S["identity"], st["ident"]
    q, dec = idt["questions"][s["q"]], idt["decline"]
    main = x["main"]
    if main == "answers" or (x["answer"] and main != "refuses"):
        value, complete, partial = normalize_answer(q, x["answer"] or x["said"], s.get("partial"), c["today"])
        if complete:
            s["partial"] = None
            return call(c, "verify", dict(question=q["field"], answer=value), dict(kind="verify", q=s["q"]))
        if partial:
            s["partial"] = partial
            return ask(c, "identity", CONTINUE)
        return ask_question(c, s["q"], reask=True)
    purpose = x["side_q"] == "purpose"
    mode = dec.get("purpose_question")
    if main == "refuses" or (purpose and mode in ("counts_as_refusal", "own_counter")):
        key = "purpose" if (main != "refuses" and mode == "own_counter") else "refusals"
        s[key] += 1
        if s[key] >= (to_int(dec.get("max_refusals")) or 2):
            return close(c, sit_ref(c, dec.get("on_declined"), "identity"))
        c["say"].append(dec["explain_template"])
        return ask_question(c, s["q"], reask=True)
    st["loops"] += 1
    if st["loops"] > LOOP_LIMIT:
        return close(c, hangup(c, "identity"))
    return ask_question(c, s["q"], reask=True)


def verify_result(c, p, r):
    S, st = c["S"], c["st"]
    idt, s = S["identity"], st["ident"]
    i = p["q"]
    q = idt["questions"][i]
    if not r["ok"]:  # unreadable answer or a failed call: ask again, not counted
        s["fails"] += 1
        if s["fails"] > LOOP_LIMIT:
            return close(c, hangup(c, "identity"))
        return ask_question(c, i, reask=True)
    s["fails"] = 0
    v = r["values"]
    if truthy(v.get("verified")):
        if q["field"] not in s["passed"]:
            s["passed"].append(q["field"])
        nxt = next_question(c, i)
        if truthy(v.get("fully_verified")) or nxt is None:
            return call(c, "lookup", dict(), dict(kind="lookup"))
        return ask_question(c, nxt)
    hint = "" if empty(v.get("hint")) else str(v.get("hint")).strip()
    h = next((h for h in idt.get("hints") or [] if h.get("value") == hint
              and h.get("question") in (q["field"], "any")), None)
    if hint and (h is None or h.get("counts_as_wrong") is not True):
        return ask(c, "identity", h["say_template"]) if h else ask_question(c, i, reask=True)
    s["wrong"] += 1
    s["qwrong"][q["field"]] = s["qwrong"].get(q["field"], 0) + 1
    lim = idt["limits"]
    left, tot, per = to_int(v.get("attempts_left")), to_int(lim.get("total_wrong")), to_int(lim.get("per_question_wrong"))
    if (left is not None and left <= 0) or (tot and s["wrong"] >= tot) or (per and s["qwrong"][q["field"]] >= per):
        return close(c, sit_ref(c, idt.get("on_failed"), "identity"))
    if h is not None:
        return ask(c, "identity", h["say_template"])
    nxt = next_question(c, i) if lim.get("on_wrong") == "next_question" else i
    return ask_question(c, i if nxt is None else nxt, reask=(nxt in (None, i)))


# ---------- account, bands, disclosure, first ask ----------

def lookup_result(c, p, r):
    st = c["st"]
    if not r["ok"]:
        return failed(c, "lookup", lambda: call(c, "lookup", dict(), dict(kind="lookup")), "identity")
    st["products"] = read_products(c)
    if not st["products"]:
        return close(c, hangup(c, "identity"))
    for prod in st["products"]:
        classify(c, prod)
    st["stage"] = "negotiate"
    first = st["products"][0]
    if len(st["products"]) == 1 and first.get("close"):  # item 14: bands that close among several products
        return close(c, first["close"])
    d = c["S"]["disclosure"]
    c["say"].append(render(d["intro_template"], values(c, dict(product_count=len(st["products"])))))
    facts = []
    for prod in st["products"]:
        c["say"].append(render(d["product_template"], product_values(c, prod)))
        facts.append(c["say"][-1])
        if prod.get("deadline"):
            facts.append("Latest promise date for " + product_name(c, prod) + ": " +
                         say_date(datetime.date.fromisoformat(prod["deadline"]), c["today"]) + ".")
    st["facts"] = join(*facts)
    return first_ask(c)


def read_products(c):
    S, v = c["S"], c["v"]
    api = S["_api"]
    items = S["bindings"]["response"]["lookup"].get("item") or dict()
    base = api_node("lookup") + "_response.data." + api["products"] + "."
    out = []
    for i in range(50):
        pre = base + str(i) + "."
        if v.get(pre + items.get("ref", "")) is None:
            break
        raw = dict()
        for name, typ in api["item_fields"].items():
            val = v.get(pre + name)
            if val is None:
                continue
            if typ in ("number", "integer"):
                val = to_num(val)
            elif typ == "boolean":
                val = truthy(val)
            raw[name] = val
        out.append(dict(ref=raw.get(items.get("ref")), kind=raw.get(items.get("kind")),
                        due=to_num(raw.get(items.get("due_amount"))), days=to_int(raw.get(items.get("days_overdue"))),
                        raw=raw))
    return out


def classify(c, p):
    """The product's band, deadline, attempts and floor, from the spec's expressions at call time."""
    P = c["S"].get("products") or dict()
    names = expr_names(c, p)
    p.update(band="", deadline="", attempts=0, close="")
    for i, b in enumerate(P.get("bands") or []):
        if expr(c, "products.bands[" + str(i) + "].selector", names):
            p["band"] = b.get("id")
            path = "products.bands[" + str(i) + "].deadline"
            if path in c["E"]:
                d = expr(c, path, names)
                p["deadline"] = (d.date() if isinstance(d, datetime.datetime) else d).isoformat()
            n = to_int(b.get("attempts"))
            p["attempts"] = 1 if n is None else n
            if b.get("action") == "close":
                p["close"] = sit_ref(c, b.get("situation"), "negotiate")
            break
    if not p["band"]:
        p["close"] = sit_ref(c, P.get("unmatched"), "negotiate")
    f = expr(c, "products.floor", names) if "products.floor" in c["E"] else None
    p["floor"] = float(p.get("due") or 0) if f is None else float(f)


def first_ask(c):
    S, st = c["S"], c["st"]
    fa = S["negotiation"]["first_ask"]
    offers = []
    for prod in st["products"]:
        names = expr_names(c, prod)
        amount = expr(c, "negotiation.first_ask.amount", names)
        date = expr(c, "negotiation.first_ask.date", names)
        if isinstance(date, datetime.datetime):
            date = date.date()
        offers.append(dict(ref=prod["ref"], amount=float(amount), date=date.isoformat(), kind="first"))
        pstate(c, prod)["offer"] = dict(offers[-1])
    st["offers"] = offers
    st["cur"] = str(st["products"][0]["ref"])
    o = offers[0]
    dt = datetime.date.fromisoformat(o["date"])
    if len(offers) > 1 and fa.get("template_multi") not in (None, NS):
        text = render(fa["template_multi"], values(c, dict(
            product_count=len(offers), total_amount=say_money(sum(x["amount"] for x in offers)),
            date=say_date(dt, c["today"]))))
    else:
        text = render(fa["template"], product_values(c, st["products"][0], dict(
            amount=say_money(o["amount"]), date=say_date(dt, c["today"]))))
    return ask(c, "negotiate", text)


# ---------- negotiation: dates and the persuasion ladder (item 12 adds amounts, events and side questions) ----------

def pstate(c, p):
    pp = c["st"]["pp"]
    k = str(p.get("ref"))
    if k not in pp:
        pp[k] = dict(used=0, vague=0, proposed=False, refused=[], offer=None, too_much=False)
    return pp[k]


def current(c):
    return product_by_ref(c, c["st"].get("cur"))


def deadline(p):
    return datetime.date.fromisoformat(p["deadline"]) if p.get("deadline") else None


def customer_day(c, text):
    """The day a customer names: a period the spec defines, else resolved against today; None when vague."""
    low = " " + re.sub(r"\s+", " ", str(text or "").lower()) + " "
    for i, np in enumerate(c["S"]["calendar"].get("named_periods") or []):
        ph = str(np.get("phrase") or "").lower().strip()
        if ph and (" " + ph + " ") in low:
            d = expr(c, "calendar.named_periods[" + str(i) + "].date", dict(today=c["today"], now=c["now"]))
            return d.date() if isinstance(d, datetime.datetime) else d
    return resolve_day(text, c["today"], c["wmode"])


def reask(c):
    st = c["st"]
    st["loops"] += 1
    if st["loops"] > LOOP_LIMIT:
        return close(c, hangup(c, st.get("stage")))
    return ask(c, st.get("stage") or "negotiate", st.get("last") or "")


def turn_negotiate(c, x):
    st, neg = c["st"], c["S"]["negotiation"]
    p = current(c)
    s = pstate(c, p)
    o = s.get("offer") or dict()
    if x["amount"]:
        a = parse_amount(x["amount"], p.get("due"), p.get("floor"))
        if a is not None:
            return propose_amount(c, p, a, customer_day(c, x["day"]) if x["day"] else None, bool(x["day"]))
    if x["day"]:
        d = customer_day(c, x["day"])
        if not (x["main"] == "refuses" and d is not None and d.isoformat() == o.get("date")):
            return propose_day(c, p, d)
    m = x["main"]
    if m == "accepts" and o:
        if o.get("kind") == "first" and len(st["offers"]) > 1:  # item 14: several products
            st["agreed"] = [dict(a) for a in st["offers"]]
            return readback(c)
        return propose_day(c, p, datetime.date.fromisoformat(o["date"]), o["amount"])
    if m == "proposes":
        return vague_day(c, p)
    if m == "too_much":
        return too_much(c, p)
    if m == "refuses":
        return refusal(c, p)
    if x["side_q"] or x["event"]:  # answered, or an event that did not apply: ask again, no attempt used
        return reask(c)
    if neg.get("non_answer_is_refusal") is False:
        return reask(c)
    return refusal(c, p)


def floor_left(c, p):
    n = to_int((c["S"]["negotiation"].get("amounts") or dict()).get("floor_offers"))
    return pstate(c, p).get("floor_n", 0) < (1 if n is None else n)


def table_date(c, p):
    o = pstate(c, p).get("offer") or dict()
    return clamp(c, p, datetime.date.fromisoformat(o["date"]) if o.get("date") else c["today"])


def offer_floor(c, p, pol, extra=None):
    s = pstate(c, p)
    s["floor_n"] = s.get("floor_n", 0) + 1
    return offer(c, p, p["floor"], table_date(c, p), "floor", pol["template"], extra=extra)


def too_much(c, p):
    pol = (c["S"]["negotiation"].get("amounts") or dict()).get("too_much") or dict()
    pstate(c, p)["too_much"] = True
    if pol.get("policy") == "offer_floor" and pol.get("template") not in (None, NS) and floor_left(c, p) and \
            float(p["floor"]) < float(p.get("due") or 0) - 0.005:
        return offer_floor(c, p, pol)
    return refusal(c, p)


def propose_amount(c, p, a, d, named_day):
    """An amount the customer proposes: accepted inside the spec's range, else the below-floor policy."""
    A = c["S"]["negotiation"].get("amounts") or dict()
    s = pstate(c, p)
    due, fl = float(p.get("due") or 0), float(p["floor"])
    lo = due if A.get("accept_range") == "due_only" else min(fl, due)
    if a >= due - 0.005:
        a = due
    else:
        s["too_much"] = True  # proposing less says the full amount is too much
    if a >= lo - 0.005:
        if named_day and d is None:
            s["offer"] = dict(s.get("offer") or dict(ref=p["ref"], date=c["today"].isoformat(), kind="customer"),
                              amount=a)
            return vague_day(c, p)
        return propose_day(c, p, d or table_date(c, p), a)
    pol = A.get("below_floor") or dict()
    if pol.get("policy") == "offer_floor" and pol.get("template") not in (None, NS) and floor_left(c, p):
        return offer_floor(c, p, pol, dict(named_amount=say_money(a)))
    return refusal(c, p)


def propose_day(c, p, d, amount=None):
    """A day for the product: agreed when it is allowed, else the spec's policy (offer another day, or ask)."""
    S, st, cal, today = c["S"], c["st"], c["cal"], c["today"]
    neg, s = S["negotiation"], pstate(c, p)
    amount = (s.get("offer") or dict()).get("amount", p.get("due") or 0) if amount is None else amount
    if d is None:
        return vague_day(c, p)
    st["stage"] = "negotiate"
    end = deadline(p)
    if d < today:
        return ask_day(c, p)
    if end is not None and d > end:
        pol = neg.get("after_deadline") or dict()
        if pol.get("policy") == "offer_deadline" and pol.get("template") not in (None, NS):
            return offer(c, p, amount, end, "deadline", pol["template"], d)
        return ask_day(c, p)
    if not is_business_day(cal, d):
        pol = neg.get("non_business_day") or dict()
        way = pol.get("policy")
        if way in ("next", "prev") and pol.get("template") not in (None, NS):
            d2 = roll(cal, d, way)
            if d2 < today:
                d2 = roll(cal, d, "next")
            if end is not None and d2 > end:
                return propose_day(c, p, d2, amount)
            return offer(c, p, amount, d2, "shift", pol["template"], d)
        return ask_day(c, p)
    st["agreed"] = [dict(ref=p["ref"], amount=float(amount), date=d.isoformat())]
    return readback(c)


def ask_day(c, p):
    st = c["st"]
    st["loops"] += 1
    if st["loops"] > LOOP_LIMIT:
        return close(c, hangup(c, "negotiate"))
    return ask(c, "negotiate", render(c["S"]["negotiation"]["ask_date_template"], product_values(c, p)))


def vague_day(c, p):
    v, s = c["S"]["negotiation"]["vague"], pstate(c, p)
    s["vague"] += 1
    n = to_int(v.get("re_asks"))
    if s["vague"] <= (1 if n is None else n):
        return ask(c, "negotiate", render(v["template"], product_values(c, p)))
    end = deadline(p)
    if v.get("then") == "propose_deadline" and end is not None and not s["proposed"] and \
            v.get("propose_template") not in (None, NS):
        s["proposed"] = True
        amount = (s.get("offer") or dict()).get("amount", p.get("due") or 0)
        return offer(c, p, amount, clamp(c, p, end), "deadline", v["propose_template"])
    return refusal(c, p)


def offer_names(c, p):
    s = pstate(c, p)
    n = expr_names(c, p)
    n.update(dict(floor=p.get("floor"), deadline=deadline(p), said_too_much=bool(s.get("too_much"))))
    return n


def clamp(c, p, d):
    """d kept a business day from today up to the product's deadline."""
    cal, today, end = c["cal"], c["today"], deadline(p)
    if isinstance(d, datetime.datetime):
        d = d.date()
    if end is not None and d > end:
        d = end
    if d < today:
        d = today
    if not is_business_day(cal, d):
        d2 = roll(cal, d, "next")
        if end is not None and d2 > end:
            d2 = roll(cal, d, "prev")
        d = d2 if d2 >= today else d
    return d


def refusal(c, p):
    """A flat refusal of the offer on the table: the next ladder step of the product's band, or the end."""
    neg, s, st = c["S"]["negotiation"], pstate(c, p), c["st"]
    o = s.get("offer")
    if o:
        s["refused"].append([round(float(o["amount"]), 2), o["date"]])
    if st.get("sup_no"):  # an event stopped persuasion: a refusal ends the call
        e = event_row(c, st["sup_no"]) or dict()
        return close(c, sit_ref(c, e.get("on_no"), "negotiate"))
    steps = [(i, x) for i, x in enumerate(neg.get("ladder") or []) if not x.get("bands") or p.get("band") in x["bands"]]
    while s["used"] < p.get("attempts", 0) and s["used"] < len(steps):
        i, step = steps[s["used"]]
        s["used"] += 1
        names = offer_names(c, p)
        amount = float(expr(c, "negotiation.ladder[" + str(i) + "].amount", names))
        d = clamp(c, p, expr(c, "negotiation.ladder[" + str(i) + "].date", names))
        if neg.get("dedupe_refused_offers") is not False and [round(amount, 2), d.isoformat()] in s["refused"]:
            continue
        same = (neg.get("amounts") or dict()).get("same_amount_template")
        if o and abs(float(o["amount"]) - amount) < 0.005 and same not in (None, "", NS) and                 s.get("floor_n") and abs(float(p["floor"]) - amount) < 0.005:  # the floor is not named twice
            return offer(c, p, amount, d, "ladder", same)
        return offer(c, p, amount, d, "ladder", step["template"])
    return close(c, sit_ref(c, neg.get("on_exhausted"), "negotiate"))


def offer(c, p, amount, d, kind, template, named=None, extra=None):
    s = pstate(c, p)
    s["offer"] = dict(ref=p["ref"], amount=float(amount), date=d.isoformat(), kind=kind)
    vals = product_values(c, p, dict(amount=say_money(amount), date=say_date(d, c["today"]),
                                     named_date=say_date(named, c["today"]) if named else ""))
    vals.update(extra or dict())
    return ask(c, "negotiate", render(template, vals))


# ---------- confirmation and recording ----------

def product_by_ref(c, ref):
    return next((p for p in c["st"]["products"] if str(p.get("ref")) == str(ref)), dict(ref=ref, raw=dict()))


def readback(c):
    S, st = c["S"], c["st"]
    cf = S["confirm"]
    st["conf"] = 0
    items = []
    for a in st["agreed"]:
        dt = datetime.date.fromisoformat(a["date"])
        items.append(render(cf["item_template"], product_values(c, product_by_ref(c, a["ref"]), dict(
            amount=say_money(a["amount"]), date=say_date(dt, c["today"])))))
    text = render(cf["readback_template"], values(c, dict(items=join(*items), question=cf["question"])))
    return ask(c, "confirm", text)


def turn_confirm(c, x):
    st = c["st"]
    a = st["agreed"][0] if st["agreed"] else None
    d = customer_day(c, x["day"]) if x["day"] else None
    if x["main"] == "yes" and a and (d is None or d.isoformat() == a["date"]):
        return record_next(c)
    if a and x["amount"] and len(st["agreed"]) == 1:  # a changed amount: checked again, then read back again
        p = product_by_ref(c, a["ref"])
        amt = parse_amount(x["amount"], p.get("due"), p.get("floor"))
        if amt is not None and abs(amt - float(a["amount"])) >= 0.005:
            pstate(c, p)["offer"] = dict(a, kind="agreed")
            return propose_amount(c, p, amt, d or datetime.date.fromisoformat(a["date"]), False)
    if a and x["day"] and len(st["agreed"]) == 1:  # a changed date: checked again, then read back again
        return propose_day(c, product_by_ref(c, a["ref"]), d, a["amount"])
    if a and x["main"] == "no" and len(st["agreed"]) == 1:  # not confirmed: a refusal of what was agreed
        p = product_by_ref(c, a["ref"])
        pstate(c, p)["offer"] = dict(a, kind="agreed")
        st["agreed"] = []
        st["stage"] = "negotiate"
        return refusal(c, p)
    if x["side_q"] and x["main"] not in ("yes", "no", "change"):  # answered: read back again, not counted
        return ask(c, "confirm", st["last"])
    st["conf"] += 1
    if st["conf"] >= 2:
        return close(c, hangup(c, "confirm"))
    return ask(c, "confirm", st["last"])


def record_next(c):
    st = c["st"]
    done = [str(r) for r in st["recorded"]]
    for a in st["agreed"]:
        if str(a["ref"]) not in done:
            return call(c, "record_promise", dict(product_ref=a["ref"], amount=a["amount"],
                                                  date=datetime.date.fromisoformat(a["date"])),
                        dict(kind="promise", ref=a["ref"]))
    return finish_recorded(c)


def promise_result(c, p, r):
    st = c["st"]
    if r["ok"]:
        st["recorded"].append(p["ref"])
        return record_next(c)
    return failed(c, "record_promise", lambda: record_next(c), "confirm", "record_promise:" + str(p["ref"]))


def finish_recorded(c):
    S, st = c["S"], c["st"]
    cf = S["confirm"]
    done = [str(r) for r in st["recorded"]]
    for a in st["agreed"]:
        if str(a["ref"]) in done:
            dt = datetime.date.fromisoformat(a["date"])
            c["say"].append(render(cf["recorded_template"], product_values(c, product_by_ref(c, a["ref"]), dict(
                amount=say_money(a["amount"]), date=say_date(dt, c["today"])))))
    if cf.get("after_recorded_sentence") not in (None, "", NS):
        c["say"].append(cf["after_recorded_sentence"])
    return close(c, sit_ref(c, cf.get("on_recorded"), "confirm"))
