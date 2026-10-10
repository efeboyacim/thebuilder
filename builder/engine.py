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
SLOTS = ("main", "side_q", "event", "answer", "day", "time", "said")
SIDE_Q = ("robot", "scam", "purpose", "other")
CONTINUE = "Please go on."
LOOP_LIMIT = 3


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
    return dict(v=vars, S=S, E=E, now=now, today=now.date(), cal=cal, H=helpers(cal), st=st, say=[], out=dict(),
                cv=cv)


def new_state():
    return dict(stage="open", wait="open", asks=1, miss=0, last="", loops=0, closing="", transferred=False,
                pending=None, third=dict(bad=0, none=0, other=0, day="", cb=None),
                ident=dict(q=0, wrong=0, qwrong=dict(), partial=None, refusals=0, purpose=0, fails=0, passed=[]),
                products=[], offers=[], agreed=[], recorded=[], fails=dict(), neg=0, conf=0)


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
    out["brain_stage"] = st.get("stage") or ""
    out["brain_error"] = ""
    out[STATE] = json.dumps(st, separators=(",", ":"))
    c["v"].update(out)


# ---------- speaking, closing, calling ----------

def ask(c, stage, text):
    st = c["st"]
    text = render(text, values(c))
    st["stage"], st["wait"], st["last"] = stage, stage, text
    c["out"]["say_next"] = join(*[render(s, values(c)) for s in c["say"] + [text]])
    c["say"] = []
    c["out"]["route"] = talk_node(stage)


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
    return hangup(c, st.get("stage"))


def close(c, sid):
    S, st = c["S"], c["st"]
    sit = situation(c, sid)
    if sit is None:
        sid = hangup(c, st.get("stage"))
        sit = situation(c, sid) or dict(say="", farewell="")
    if sit.get("queue") and "transfer" in S["bindings"]["tools"] and not st.get("transferred"):
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
        c["st"]["transferred"] = True
        return close(c, p.get("sid"))
    raise ValueError("unknown pending call " + str(kind))


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
    filled = any(x[k] for k in ("main", "side_q", "event", "answer", "day", "time"))
    if not filled and not fallback_main(c, stage, x):
        if not st.get("miss"):
            st["miss"] = 1
            return ask(c, stage, st.get("last") or "")
        x["main"] = "unclear"
    st["miss"] = 0
    t = termination(c, stage, x)
    if t:
        return close(c, t)
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
    question = x["side_q"] in SIDE_Q
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
    if x["side_q"] in SIDE_Q or x["main"] == "offers_to_pay":
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
    d = resolve_day(x["day"], today) if x["day"] else None
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
    c["st"]["third"]["cb"] = None
    return close(c, hangup(c, "third"))  # item 12: failure_policy for book_callback


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


# ---------- account, disclosure, first ask ----------

def lookup_result(c, p, r):
    st = c["st"]
    if not r["ok"]:
        st["fails"]["lookup"] = st["fails"].get("lookup", 0) + 1
        if st["fails"]["lookup"] <= 1:  # item 12: failure_policy for lookup
            return call(c, "lookup", dict(), dict(kind="lookup"))
        return close(c, hangup(c, "identity"))
    st["products"] = read_products(c)
    if not st["products"]:
        return close(c, hangup(c, "identity"))
    st["stage"] = "negotiate"
    d = c["S"]["disclosure"]
    c["say"].append(render(d["intro_template"], values(c, dict(product_count=len(st["products"])))))
    for prod in st["products"]:
        c["say"].append(render(d["product_template"], product_values(c, prod)))
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
        offers.append(dict(ref=prod["ref"], amount=float(amount), date=date.isoformat()))
    st["offers"] = offers
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


# ---------- negotiation (slice: the first ask only; item 11 adds dates, amounts and the ladder) ----------

def turn_negotiate(c, x):
    st = c["st"]
    if x["main"] == "accepts":
        st["agreed"] = [dict(o) for o in st["offers"]]
        return readback(c)
    st["neg"] += 1
    if st["neg"] >= 2:
        return close(c, hangup(c, "negotiate"))
    return ask(c, "negotiate", st["last"])


# ---------- confirmation and recording ----------

def product_by_ref(c, ref):
    return next((p for p in c["st"]["products"] if str(p.get("ref")) == str(ref)), dict(ref=ref, raw=dict()))


def readback(c):
    S, st = c["S"], c["st"]
    cf = S["confirm"]
    items = []
    for a in st["agreed"]:
        dt = datetime.date.fromisoformat(a["date"])
        items.append(render(cf["item_template"], product_values(c, product_by_ref(c, a["ref"]), dict(
            amount=say_money(a["amount"]), date=say_date(dt, c["today"])))))
    text = render(cf["readback_template"], values(c, dict(items=join(*items), question=cf["question"])))
    return ask(c, "confirm", text)


def turn_confirm(c, x):
    st = c["st"]
    if x["main"] == "yes":
        return record_next(c)
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
    return close(c, hangup(c, "confirm"))  # item 11: retry and failure_policy


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
