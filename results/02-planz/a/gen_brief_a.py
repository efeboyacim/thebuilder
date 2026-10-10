"""Generate out/brief-a.workflow.json for Brief A (Plan Z, hand-designed by the agentic builder).

    python out/gen_brief_a.py tools/openapi.json out/brief-a.workflow.json
"""
import json, sys

api = json.load(open(sys.argv[1], encoding="utf-8"))
BASE = api["servers"][0]["url"].rstrip("/")
OUT = sys.argv[2]


def schema(tool):
    return api["paths"]["/" + tool]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]


def api_tool(name, fields, desc, required=(), mapping=(), pre="", post="", retries=0, engine=False):
    """fields: {field: (default, description)}. type and enum come from the tool API file."""
    props = {}
    sp = schema(name)
    for f, (default, d) in fields.items():
        leaf = {"type": sp[f].get("type", "string"), "default": default}
        if d:
            leaf["description"] = d
        if "enum" in sp[f]:
            leaf["enum"] = sp[f]["enum"]
        props[f] = leaf
    t = {
        "type": "function",
        "config": {"timeoutMs": 12000, "maxRetries": retries},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": desc,
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": BASE + "/" + name},
                "method": {"type": "string", "default": "POST"},
                "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props, "required": list(required)},
            }},
        },
    }
    if mapping and not engine:
        t["response_mapping"] = [{"path": p, "variable": v} for p, v in mapping]
    if pre:
        t["pre_process_code"] = pre
    if post:
        t["post_process_code"] = post
    return t


# ---------------------------------------------------------------- code run in the workflow
FACTS_CODE = r'''import datetime, json, re
def _facts():
    HOL = set(['2026-10-26', '2026-11-11', '2026-11-26', '2026-12-25', '2027-01-01'])
    DN = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    MN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    D1 = datetime.timedelta(days=1)
    s = str(vars.get('now') or '').strip().replace(' ', 'T')
    try:
        now = datetime.datetime.fromisoformat(s[:16])
    except Exception:
        try:
            now = datetime.datetime.fromisoformat(s[:10] + 'T09:00')
        except Exception:
            now = datetime.datetime.utcnow()
    today = now.date()
    def isb(d):
        return d.weekday() < 5 and d.isoformat() not in HOL
    def nextb(d):
        while not isb(d):
            d = d + D1
        return d
    def addb(d, n):
        c = 0
        while c < n:
            d = d + D1
            if isb(d):
                c += 1
        return d
    def say(d):
        return DN[d.weekday()] + ' ' + str(d.day) + ' ' + MN[d.month - 1] + ' ' + str(d.year) + ' (' + d.isoformat() + ')'
    def money(x):
        return '{:,.2f}'.format(float(x))
    vars['today_text'] = say(today)
    vars['now_time'] = now.strftime('%H:%M')
    nb = nextb(today + D1)
    cb = []
    if now.strftime('%H:%M') < '19:00':
        start = '10:00' if now.strftime('%H:%M') < '10:00' else 'later than ' + now.strftime('%H:%M')
        cb.append('- Today, ' + say(today) + ': from ' + start + ' up to 19:00 included.')
    else:
        cb.append('- Today is no longer possible (it is after 19:00).')
    cb.append('- Next business day, ' + say(nb) + ': from 10:00 to 19:00, both included.')
    cb.append('- Any other day, or a time before 10:00 or after 19:00, is outside the window.')
    vars['callback_facts'] = '\n'.join(cb)
    monday = today - datetime.timedelta(days=today.weekday())
    wk = []
    for i in range(7):
        a = monday + datetime.timedelta(days=i)
        b = a + datetime.timedelta(days=7)
        wk.append('- "this ' + DN[i] + '" = ' + say(a) + '; "next ' + DN[i] + '" = ' + say(b))
    wk.append('- "tomorrow" = ' + say(today + D1))
    vars['weekday_facts'] = '\n'.join(wk)
    # ---- products (only after get_account)
    raw = vars.get('products')
    items = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            try:
                raw = json.loads(raw.replace("'", '"').replace('True', 'true').replace('False', 'false').replace('None', 'null'))
            except Exception:
                raw = None
    if isinstance(raw, dict):
        raw = raw.get('products')
    if isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
    if not items:
        found = {}
        for k, v in list(vars.items()):
            m = re.search(r'products\W+(\d+)\W+(\w+)$', str(k))
            if m:
                found.setdefault(int(m.group(1)), {})[m.group(2)] = v
        items = [found[i] for i in sorted(found)]
    if not items:
        vars['account_state'] = 'missing' if raw is not None or vars.get('load_account_response.status') else 'not_loaded'
        return
    p = items[0]
    t = str(p.get('type') or '')
    if t == 'credit_card':
        phrase = 'credit card ending ' + str(p.get('card_last4') or '')
        floor = p.get('minimum_payment'); floor_name = 'the minimum payment'
    elif t == 'personal_loan':
        phrase = 'personal loan'; floor = p.get('instalment_amount'); floor_name = 'the instalment amount'
    elif t == 'car_loan':
        phrase = 'car loan'; floor = p.get('instalment_amount'); floor_name = 'the instalment amount'
    else:
        phrase = t.replace('_', ' '); floor = p.get('minimum_payment') or p.get('instalment_amount'); floor_name = 'the floor'
    full = float(p.get('past_due') or 0)
    floor = float(floor or 0)
    dl = int(float(p.get('days_late') or 0))
    late = dl >= 8
    deadline = addb(today, 7 if late else 12)
    attempts = 2 if late else 1
    fri = monday + datetime.timedelta(days=4)
    a1 = fri
    while a1 >= monday and not isb(a1):
        a1 = a1 - D1
    if a1 <= today:
        a1 = monday + datetime.timedelta(days=11)
        while not isb(a1):
            a1 = a1 - D1
    if a1 > deadline:
        a1 = deadline
    vars['product_id'] = str(p.get('product_id') or 'p1')
    vars['product_phrase'] = phrase
    vars['past_due_text'] = money(full)
    vars['days_late_text'] = str(dl)
    vars['floor_value'] = '%.2f' % floor
    vars['full_value'] = '%.2f' % full
    vars['deadline_iso'] = deadline.isoformat()
    f = []
    f.append('- Product: ' + phrase + '.')
    f.append('- Full past-due amount: ' + money(full) + '. Days late: ' + str(dl) + '. Band: ' + ('late' if late else 'early') + '.')
    f.append('- Floor (' + floor_name + '): ' + money(floor) + '. An amount from ' + money(floor) + ' up to ' + money(full) + ', both included, can be recorded. Below ' + money(floor) + ' cannot be recorded. More than ' + money(full) + ' cannot be recorded.')
    f.append('- Today: ' + say(today) + ', time ' + now.strftime('%H:%M') + '.')
    f.append('- DEADLINE (latest date you may record): ' + say(deadline) + '.')
    f.append('- Persuasion attempts allowed: ' + str(attempts) + '.')
    f.append('- Attempt 1: propose the full amount ' + money(full) + ' (or the floor ' + money(floor) + ' if the customer already said the full amount is too much) on ' + say(a1) + '.')
    if late:
        f.append('- Attempt 2: propose the floor ' + money(floor) + ' on the deadline, ' + say(deadline) + '.')
    else:
        f.append('- There is no attempt 2 for this customer. After attempt 1 is refused, close as no_promise / refused.')
    vars['debt_facts'] = '\n'.join(f)
    cal = []
    d = today
    while d <= deadline:
        if isb(d):
            cal.append('- ' + say(d) + (' (today)' if d == today else '') + ': business day, allowed.')
        else:
            why = 'weekend' if d.weekday() >= 5 else 'bank holiday'
            cal.append('- ' + say(d) + ': ' + why + ', NOT allowed. Offer the next business day, ' + say(nextb(d)) + ', instead.')
        d = d + D1
    cal.append('- Every date after ' + say(deadline) + ' is AFTER THE DEADLINE: say the latest date you can record is ' + say(deadline) + ' and offer it.')
    cal.append('- Every date before today is in the past and not allowed.')
    vars['calendar_facts'] = '\n'.join(cal)
    vars['account_state'] = 'loaded'
try:
    _facts()
except Exception as e:
    vars['account_state'] = 'error'
    for k in ['today_text', 'callback_facts', 'weekday_facts', 'debt_facts', 'calendar_facts', 'product_phrase', 'past_due_text', 'days_late_text']:
        if not vars.get(k):
            vars[k] = 'unknown'
'''

VERIFY_POST = r'''try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            def tb(x):
                return str(x).lower() == 'true'
            left = d.get('wrong_answers_left')
            if tb(d.get('all_verified')):
                msg = 'Both security answers are verified. Ask no more security questions.'
            elif d.get('hint') == 'day_month_swapped':
                msg = 'Day and month look swapped. This does NOT count as a wrong answer. Ask the customer to say their date of birth again with the month as a word, then call verify_identity again.'
            elif str(left) == '0':
                msg = 'Three wrong answers: the identity check has failed. Do not state any account details.'
            elif tb(d.get('verified')):
                msg = 'This answer is correct. Now ask the other security question, the one not yet verified. Ask only that one question.'
            elif 'verified' not in d and not tb(d.get('ok')):
                msg = 'The answer could not be read and was NOT checked; it does not count as wrong. Ask the customer to say it again.'
            else:
                msg = 'This answer is WRONG. Say it does not match our records and ask the SAME question again. Wrong answers left: ' + str(left) + '.'
            d['what_to_do_next'] = msg
except Exception:
    pass
'''

PROMISE_PRE = r'''try:
    b = config.get('body')
    if isinstance(b, dict):
        if vars.get('product_id'):
            b['product_id'] = str(vars.get('product_id'))
        if vars.get('customer_id'):
            b['customer_id'] = str(vars.get('customer_id'))
        try:
            b['amount'] = round(float(str(b.get('amount')).replace(',', '')), 2)
        except Exception:
            pass
        b['date'] = str(b.get('date') or '')[:10]
except Exception:
    pass
'''

PROMISE_POST = r'''try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = {'ok': False}
            result['data'] = d
        if str(d.get('ok')).lower() == 'true':
            try:
                vars['promise_done'] = 'yes'
            except Exception:
                pass
            d['what_to_do_next'] = 'The promise IS recorded. In ONE message: tell the customer it is recorded, say exactly "You will receive a text message confirming this promise.", thank them, say goodbye, and call close_call with outcome promise.'
        else:
            n = 0
            try:
                n = int(vars.get('promise_fails') or 0) + 1
                vars['promise_fails'] = str(n)
            except Exception:
                n = 0
            if n == 1:
                d['what_to_do_next'] = 'The promise was NOT recorded (service error). Do NOT say it is recorded. Call create_promise again now, once, with exactly the same amount and date.'
            elif n >= 2:
                d['what_to_do_next'] = 'The promise was NOT recorded again. Do not try again. In ONE message: tell the customer you could not record the promise, thank them, say goodbye, and call close_call with outcome no_promise and reason service_error.'
            else:
                d['what_to_do_next'] = 'The promise was NOT recorded. Do NOT say it is recorded. If this is the first failure, call create_promise again once with the same values. If it already failed before, tell the customer you could not record it, say goodbye and call close_call with no_promise / service_error.'
except Exception:
    pass
'''

TRANSFER_PRE = r'''try:
    b = config.get('body')
    if isinstance(b, dict):
        vars['transfer_queue'] = str(b.get('queue') or '')
except Exception:
    pass
'''

TRANSFER_POST = r'''try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            if str(d.get('ok')).lower() == 'true':
                d['what_to_do_next'] = 'The transfer is done. In ONE message: tell the customer you are connecting them now, thank them, say goodbye, and call close_call with outcome transferred and the matching reason (restructuring queue: restructuring; general queue: human; support queue: support).'
            else:
                d['what_to_do_next'] = 'The transfer did not go through. Tell the customer, then call transfer_to_agent once more.'
except Exception:
    pass
'''

CALLBACK_POST = r'''try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            if str(d.get('ok')).lower() == 'true':
                try:
                    vars['callback_done'] = 'yes'
                except Exception:
                    pass
                d['what_to_do_next'] = 'The callback is booked. In ONE message: confirm the day and time, thank the person, say goodbye, and call close_call with outcome not_reached and reason callback.'
            else:
                d['what_to_do_next'] = 'The callback was NOT booked. Check the date (YYYY-MM-DD) and time (HH:MM) against the allowed window and try once more.'
except Exception:
    pass
'''

CLOSE_PRE = r'''try:
    b = config.get('body')
    if isinstance(b, dict):
        v = vars
        def tb(x):
            return str(x).lower() == 'true'
        def num(x):
            try:
                return int(float(x))
            except Exception:
                return 0
        o = str(b.get('outcome') or '')
        r = str(b.get('reason') or '')
        q = str(v.get('transfer_queue') or v.get('transfer_to_agent_args.queue') or '')
        tok = v.get('transfer_ok')
        RM = dict(restructuring='restructuring', general='human', support='support')
        promised = v.get('promise_done') == 'yes' or bool(str(v.get('promise_id') or '').strip()) or tb(v.get('promise_ok'))
        verified = tb(v.get('id_all_verified')) or tb(v.get('verify_identity_response.data.all_verified')) or bool(v.get('product_id'))
        left = str(v.get('id_left') if v.get('id_left') is not None else v.get('verify_identity_response.data.wrong_answers_left'))
        fails = max(num(v.get('promise_fails')), num(v.get('create_promise_failure_count')))
        if q in RM and (tok is None or tb(tok)):
            o, r = 'transferred', RM[q]
        elif promised:
            o, r = 'promise', ''
        elif not verified and left == '0':
            o, r = 'not_verified', 'failed'
        elif not verified and o == 'no_promise':
            o, r = 'not_verified', 'declined'
        elif o == 'promise' and fails >= 2:
            o, r = 'no_promise', 'service_error'
        elif v.get('callback_done') == 'yes' and o == 'not_reached':
            r = 'callback'
        if o:
            b['outcome'] = o
        if o in ('promise', 'paid') or not r:
            b.pop('reason', None)
        else:
            b['reason'] = r
        if v.get('customer_id'):
            b['customer_id'] = str(v.get('customer_id'))
except Exception:
    pass
'''

CLOSE_POST = r'''try:
    if isinstance(result, dict):
        result.pop('header', None)
except Exception:
    pass
'''

GET_ACCOUNT_POST = CLOSE_POST

for name, c in list(globals().items()):
    if name.isupper() and isinstance(c, str) and name not in ("BASE", "OUT") and ("{{" in c or "}}" in c):
        raise SystemExit("braces in " + name)

# ---------------------------------------------------------------- tools
REASONS = ["refused", "hardship", "dispute", "service_error", "failed", "declined", "callback", "no_callback",
           "wrong_number", "deceased", "unclear", "restructuring", "human", "support"]


def close_tool():
    t = api_tool("close_call", {
        "customer_id": ("{{customer_id}}", ""),
        "outcome": ("{{close_outcome}}", "How the call ended. promise: a promise was recorded. paid: the customer already paid. transferred: handed to a team. no_promise: verified customer, no promise. not_verified: identity check did not pass. not_reached: the right person was not on the line."),
    }, "Record how the call ended. CALL it exactly once, as the very last action of the call, in the same message as your goodbye. Never call any tool after it.",
        required=["outcome"], pre=CLOSE_PRE, post=CLOSE_POST)
    t["function"]["parameters"]["properties"]["body"]["properties"]["reason"] = {
        "type": "string", "default": "{{close_reason}}", "enum": REASONS,
        "description": "Leave out for promise and paid. transferred: restructuring, human or support. no_promise: refused, hardship, dispute or service_error. not_verified: failed or declined. not_reached: callback, no_callback, wrong_number, deceased or unclear."}
    return t


verify = api_tool("verify_identity", {
    "customer_id": ("{{customer_id}}", ""),
    "field": ("{{security_field}}", "Which question this answer is for: date_of_birth or phone_last4."),
    "answer": ("{{security_answer}}", "date_of_birth: ALWAYS as YYYY-MM-DD. Convert words (14 March 1988 -> 1988-03-14). A date said only as numbers is day, then month, then year (05/03/1990 -> 1990-03-05). phone_last4: exactly four digits."),
}, "Check one security answer. CALL it every time the customer gives a complete answer to a security question. For the phone number wait until you have all four digits.",
    required=["field", "answer"], mapping=[("data.all_verified", "id_all_verified"), ("data.wrong_answers_left", "id_left")], post=VERIFY_POST)

promise = api_tool("create_promise", {
    "customer_id": ("{{customer_id}}", ""),
    "product_id": ("{{product_id}}", "The product id."),
    "amount": ("{{promise_amount}}", "The agreed amount, a number with up to 2 decimals, e.g. 637.5."),
    "date": ("{{promise_date}}", "The agreed payment date as YYYY-MM-DD, taken from the calendar."),
}, "Record the payment promise. CALL it only after the customer clearly said yes to your read-back of product, amount and date.",
    required=["amount", "date"], mapping=[("data.promise_id", "promise_id"), ("data.ok", "promise_ok")], pre=PROMISE_PRE, post=PROMISE_POST)

transfer = api_tool("transfer_to_agent", {
    "customer_id": ("{{customer_id}}", ""),
    "queue": ("{{transfer_queue_choice}}", "restructuring: new payment plan or lower instalments. general: the customer insists on a human. support: hardship (illness, death in the family, job loss) and the customer accepted the support team."),
}, "Hand the customer to a team. CALL it only in the situations listed in your instructions.",
    required=["queue"], mapping=[("data.ok", "transfer_ok")], pre=TRANSFER_PRE, post=TRANSFER_POST)

callback = api_tool("schedule_callback", {
    "customer_id": ("{{customer_id}}", ""),
    "date": ("{{callback_day}}", "Callback date as YYYY-MM-DD, inside the allowed window."),
    "time": ("{{callback_hour}}", "Callback time as HH:MM, 24 hour, inside the allowed window."),
}, "Book a callback. CALL it when the person names a day and time inside the allowed callback window.",
    required=["date", "time"], mapping=[("data.ok", "callback_ok")], post=CALLBACK_POST)

get_account = api_tool("get_account", {"customer_id": ("{{customer_id}}", "")}, "Get the late products.", retries=2,
                       post=GET_ACCOUNT_POST, engine=True)

close_failed = api_tool("close_call", {
    "customer_id": ("{{customer_id}}", ""),
    "outcome": ("not_verified", ""),
}, "Close the call.", engine=True)
close_failed["function"]["parameters"]["properties"]["body"]["properties"]["reason"] = {"type": "string", "default": "failed"}


def code_node(name, nxt):
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                                                          "parameters": {"type": "object", "properties": {
                                                              "code": {"type": "string", "default": FACTS_CODE},
                                                              "timeout_ms": {"type": "number", "default": 10000}}}}}],
        "transitions": [{"target_node": nxt, "description": "always", "condition": {"operator": "always"}}],
    }


def grp(*conds):
    return {"logic": "or", "conditions": list(conds)}


def eq(var, val):
    return {"variable": var, "operator": "equals", "operand": val}


CLOSED = {"target_node": "end", "description": "close_call has been called",
          "condition": grp({"variable": "close_call_response.status", "operator": "is_set"},
                           {"variable": "close_call_success_count", "operator": "greater_than", "operand": 0},
                           {"variable": "close_call_failure_count", "operator": "greater_than", "operand": 0})}


def conv(name, task, tools, transitions, on_enter=None, turns=30):
    n = {"name": name, "node_type": "conversation", "task_message": task, "tools": tools, "transitions": transitions,
         "max_node_turns": turns, "max_turn_transition": "end", "truncation_strategy": "keep_all", "interruptible": True}
    if on_enter:
        n["on_enter"] = on_enter
        n["on_enter_first_entry_only"] = True
    return n


SYSTEM = """You are Freya Bank's digital assistant on an outbound phone call to {{title}} {{first_name}} {{last_name}}. Speak English. Say one or two short sentences per turn. Address the customer as {{title}} {{last_name}}.
Current date and time of this call: {{now}} (today is {{today_text}}). Never use any other clock.
General rules for the whole call:
- If asked whether you are a robot or an AI: say you are Freya Bank's digital assistant, and continue.
- If asked whether this is a scam: say they can verify the call by phoning Freya Bank's customer service number, and continue.
- Never invent figures, dates or reasons. If you do not have some information, say you do not have that information.
- Tool results may contain what_to_do_next. Always do what it says.
- After every tool result, speak to the customer or call the next tool. Never end a turn with nothing. Call at most one tool per message.
- When a rule says to call a tool, call the tool. Saying you did it is not enough.
- To end the call: thank the person and say goodbye, and in that same message call close_call. close_call is always the last tool call and is called exactly once."""

OPENING = """You have said who you are and asked whether you are speaking with {{title}} {{last_name}}. Your only job in this step is to find out whether {{title}} {{last_name}} is on the line.
- Do not say why you are calling. Never mention collections, a debt, a payment, an amount, an account or a product.
- Only "yes", "that's me", "speaking" or the customer's own name counts as confirmation. "Hello", "go ahead" and "who is this" do NOT count.
- If it is not confirmed, ask once more: "Am I speaking with {{title}} {{last_name}}?". If the person asked a question, answer it first (you are Freya Bank's digital assistant; for a scam worry, they can verify the call by phoning Freya Bank's customer service number), then ask. That ask is the second ask.
- If it is still unclear after the second ask: thank them, say goodbye and call close_call with outcome not_reached and reason unclear.
- No other tool is allowed in this step."""

THIRD = """The person on the line is not {{title}} {{last_name}}, or says the number is wrong, or says the customer has died.
STRICT PRIVACY: Do not say why you are calling. Say nothing beyond: you are Freya Bank's digital assistant and the call is recorded. Never mention collections, a debt, a payment, an amount, an account or a product, even if they ask or offer to pay. Do not ask or accept security answers from them. If they ask what it is about or offer to pay, say you can only discuss it with {{title}} {{last_name}} personally.
What to do:
- The number does not belong to the customer: apologise, thank them, say goodbye and call close_call with not_reached / wrong_number.
- The customer has died: say exactly "I am sorry for your loss.", ask nothing else, say goodbye and call close_call with not_reached / deceased.
- Otherwise ask when {{first_name}} {{last_name}} can be reached: which day and what time.
ALLOWED CALLBACK WINDOW (computed for this call, trust it):
{{callback_facts}}
- If they give a day but no time, or a vague time, ask for the time. "Morning around eleven" means 11:00.
- If the day and time are inside the window: call schedule_callback with the date (YYYY-MM-DD) and time (HH:MM). Then thank them, say goodbye and call close_call with not_reached / callback.
- If the day or time is outside the window: say that is not possible and ask ONCE for another time, naming the allowed window. If the second answer is also outside the window, or they cannot say: do not schedule anything; thank them, say goodbye and call close_call with not_reached / no_callback."""

IDENTITY = """The person confirmed they are {{title}} {{last_name}}. Verify their identity before anything else.
- Do not mention collections, a debt, a payment, an amount or a product, and do not say why you are calling. If asked, say you can explain right after the security check.
- Ask the two security questions one at a time: first the date of birth, then the last four digits of the phone number on file.
- Call verify_identity for every complete answer, then do what the result's what_to_do_next says. A wrong answer may be asked again. If the result hints that day and month are swapped, ask them to say the month as a word; that retry does not count as wrong.
- If they will not answer, or ask what the call is about instead of answering: explain once that these questions protect their account, and ask again. If they refuse a second time or ask that a second time: thank them, say goodbye and call close_call with not_verified / declined. Saying twice that they do not want to talk is the same: not_verified / declined.
- If they ask for a human: ask once whether you can help them first. If they insist, call transfer_to_agent with queue general, then say goodbye and call close_call with transferred / human."""

COLLECTION = """The customer is verified. You have told them the debt and asked "Can you make this payment today?". Get a payment promise where you can, following these rules exactly, then close the call. The tools you may use: create_promise, transfer_to_agent, close_call.

FACTS FOR THIS CALL (computed; trust them, never compute dates or amounts yourself):
{{debt_facts}}

CALENDAR UP TO THE DEADLINE (look up every day the customer names here):
{{calendar_facts}}
Weekday names:
{{weekday_facts}}
"Next <weekday>" means that weekday in the following calendar week. "This <weekday>" means the one in the current week.

SPECIAL SITUATIONS (check these first; they come before any persuasion)
- Illness, a death in the family or job loss: say ONE short sentence of sympathy. Make no persuasion attempt. Offer to connect them with the customer support team. If they accept: call transfer_to_agent with queue support. If they decline: thank, goodbye, close_call no_promise / hardship. If they still name a date or an amount themselves, handle it as a promise below.
- They want a new payment plan or lower instalments: describe no plan; call transfer_to_agent with queue restructuring right away.
- They say they already paid this past-due amount: say that payments can take one business day to appear, take no promise, thank, goodbye, close_call with outcome paid.
- "This debt is not mine", "I never had this product", or the product was closed or paid off in the past: do not argue, take no promise, thank, goodbye, close_call no_promise / dispute.
- They want a human: ask once whether you can help first. If they insist: transfer_to_agent with queue general.
- They say twice that they do not want to talk: thank, goodbye, close_call no_promise / refused.
- Never offer a discount, a waiver, an instalment plan or any term that is not written here. If asked, say you cannot offer that.

AMOUNTS
- You propose every amount. NEVER ask the customer for an amount in any form: not "how much can you pay?", not "which amount?", not "what date and amount?". When you ask a question, ask only about the day, and always name the amount yourself. If the customer asks you to suggest, propose per these rules.
- Ask for the full past-due amount first.
- If the customer says the full amount is too much: offer the floor, once, and say that the rest stays overdue. For example: "I can record a payment of <floor> instead; the rest stays overdue. Can you pay <floor> today?"
- An amount the customer proposes from the floor up to the full amount, both included, is accepted.
- Below the floor: say it cannot be recorded and offer the floor once. If they do not accept the floor, that is a flat refusal.
- Refusing the floor is a flat refusal.

DATES
- A promise date must be today or later, a business day, and not after the deadline. Check it in the calendar above.
- A weekend day or holiday that is not after the deadline: offer the next business day given in the calendar.
- A date after the deadline: say the latest date you can record is the deadline and offer the deadline. If they refuse the deadline, that is a flat refusal.
- A vague period ("next week", "end of the month", "when my salary comes"): ask ONCE for the exact day. If they still give no day, propose the deadline.

PERSUASION
- A date or an amount the customer proposes is never a refusal. Handle it with AMOUNTS and DATES.
- A flat refusal or a non-answer uses one persuasion attempt. A "no" to paying today with no date and no amount is a flat refusal.
- Use the attempt offers from the facts, in order, and only as many as allowed. Never repeat an offer the customer already refused.
- When the allowed attempts are used up and the customer still refuses: stop, thank, goodbye, close_call no_promise / refused.

CONFIRM AND RECORD
- When amount and date are agreed and valid, read back the product, the amount and the date (weekday, day and month), and ask "Do you confirm?".
- Call create_promise only after a clear yes. If they change the date or amount, check it again and read it back again.
- Never say the promise is recorded before create_promise returned ok true. Follow what_to_do_next in its result when there is one.
- IF create_promise FAILS (an error, "service unavailable", or ok false), nothing was recorded. Do not give up and do not say goodbye: call create_promise AGAIN right away, exactly once more, with the same amount and date. Only if that second call also fails: tell the customer you could not record the promise, thank them, say goodbye and call close_call with no_promise / service_error.
- After a recorded promise say: "You will receive a text message confirming this promise." Only one promise; if they want to change it later, they can do so in the mobile app.

CLOSING: outcome and reason for close_call:
- promise (no reason): a promise was recorded.
- paid (no reason): they already paid.
- transferred: restructuring, human or support, after transfer_to_agent.
- no_promise: refused, hardship, dispute or service_error."""

nodes = {
    "setup": code_node("setup", "opening"),
    "opening": conv("opening", OPENING, [close_tool()], [
        CLOSED,
        {"target_node": "identity", "description": "The person confirmed they are {{title}} {{last_name}}",
         "condition": {"operator": "matches_intent", "description": "In the last message the person clearly confirms they are {{title}} {{first_name}} {{last_name}}: they say yes, that's me, speaking, or say their own name. A greeting such as hello or go ahead, or a question such as who is this, is NOT a confirmation."}},
        {"target_node": "third_party", "description": "Someone else answered, or wrong number, or the customer died",
         "condition": {"operator": "matches_intent", "description": "The person says they are NOT {{first_name}} {{last_name}} (for example a relative, a spouse or a colleague answering), or that the customer is not available or not at home, or that this number does not belong to the customer, or that the customer has died."}},
    ], on_enter={"type": "tts", "text": "Hello, this is Freya Bank's digital assistant calling, and this call is recorded. Am I speaking with {{title}} {{last_name}}?"}),
    "third_party": conv("third_party", THIRD, [callback, close_tool()], [CLOSED]),
    "identity": conv("identity", IDENTITY, [verify, transfer, close_tool()], [
        CLOSED,
        {"target_node": "load_account", "description": "both security answers verified",
         "condition": grp(eq("id_all_verified", "true"), eq("id_all_verified", "True"),
                          eq("verify_identity_response.data.all_verified", "true"),
                          eq("verify_identity_response.data.all_verified", "True"))},
        {"target_node": "close_id_failed", "description": "three wrong answers",
         "condition": grp(eq("id_left", "0"), eq("verify_identity_response.data.wrong_answers_left", "0"))},
    ], on_enter={"type": "tts", "text": "Thank you. Before we continue, I need to ask you two security questions. What is your date of birth?"}),
    "load_account": {
        "name": "load_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "compute",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [get_account],
        "response_mapping": [{"path": "data.products", "variable": "products"}],
        "transitions": [{"target_node": "compute", "description": "always", "condition": {"operator": "always"}}],
    },
    "compute": code_node("compute", "collection"),
    "collection": conv("collection", COLLECTION, [promise, transfer, close_tool()], [CLOSED], turns=60,
                       on_enter={"type": "tts", "text": "Thank you, {{title}} {{last_name}}, you are verified. I am calling from the Freya Bank collections team about a late payment on your {{product_phrase}}. The past-due amount is {{past_due_text}}, and the payment is {{days_late_text}} days late. Can you make this payment today?"}),
    "close_id_failed": {
        "name": "close_id_failed", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "end",
        "on_enter": {"type": "tts", "text": "I am sorry, I could not verify your identity, so I cannot discuss anything further on this call. Please use the Freya Bank mobile app or visit a branch. Thank you for your time, goodbye."},
        "disable_inactivity_monitor": True, "pre_actions": [close_failed],
        "transitions": [{"target_node": "end", "description": "always", "condition": {"operator": "always"}}],
    },
    "end": {
        "name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "transitions": [],
        "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                                                      "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
    },
}

wf = {
    "initial_node": "setup",
    "system_prompt": SYSTEM,
    "nodes": nodes,
    "global_nodes": {},
    "tools": [],
    "test_variables": {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"},
}


def no_null(x, path="wf"):
    if x is None:
        raise SystemExit("null at " + path)
    if isinstance(x, dict):
        for k, v in x.items():
            no_null(v, path + "." + k)
    if isinstance(x, list):
        for i, v in enumerate(x):
            no_null(v, path + "[" + str(i) + "]")


no_null(wf)
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    json.dump(wf, f, indent=1, ensure_ascii=False)
print("wrote", OUT, len(nodes), "nodes")
