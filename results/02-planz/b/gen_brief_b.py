"""Generate out/brief-b.workflow.json for Brief B (one to three products) (Plan Z, hand-designed by the agentic builder).

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
    title = str(vars.get('title') or '').strip()
    last = str(vars.get('last_name') or '').strip()
    fri = monday + datetime.timedelta(days=4)
    a1 = fri
    while a1 >= monday and not isb(a1):
        a1 = a1 - D1
    if a1 <= today:
        a1 = monday + datetime.timedelta(days=11)
        while not isb(a1):
            a1 = a1 - D1
    ids = []
    names = []
    sentences = []
    f = []
    deadlines = []
    for idx, p in enumerate(items):
        pid = str(p.get('product_id') or ('p' + str(idx + 1)))
        t = str(p.get('type') or '')
        if t == 'credit_card':
            phrase = 'credit card ending ' + str(p.get('card_last4') or '')
            floor = p.get('minimum_payment'); floor_name = 'minimum payment'
        elif t == 'personal_loan':
            phrase = 'personal loan'; floor = p.get('instalment_amount'); floor_name = 'instalment amount'
        elif t == 'car_loan':
            phrase = 'car loan'; floor = p.get('instalment_amount'); floor_name = 'instalment amount'
        else:
            phrase = t.replace('_', ' '); floor = p.get('minimum_payment') or p.get('instalment_amount'); floor_name = 'floor'
        full = float(p.get('past_due') or 0)
        floor = float(floor or 0)
        dl = int(float(p.get('days_late') or 0))
        late = dl >= 8
        deadline = addb(today, 7 if late else 12)
        deadlines.append((deadline, pid, phrase))
        pa1 = a1 if a1 <= deadline else deadline
        ids.append(pid)
        names.append(pid + ' = ' + phrase)
        if len(items) == 1:
            sentences.append('The past-due amount on your ' + phrase + ' is ' + money(full) + ', and the payment is ' + str(dl) + ' days late.')
        else:
            sentences.append('Your ' + phrase + ' has a past-due amount of ' + money(full) + ' and is ' + str(dl) + ' days late.')
        f.append('PRODUCT ' + str(idx + 1) + ': ' + phrase + ' (product_id ' + pid + ')')
        f.append('  - Full past-due amount: ' + money(full) + '. Days late: ' + str(dl) + '. Band: ' + ('late' if late else 'early') + '.')
        f.append('  - Floor (' + floor_name + '): ' + money(floor) + '. Recordable amounts: from ' + money(floor) + ' up to ' + money(full) + ', both included. Below ' + money(floor) + ' or above ' + money(full) + ' cannot be recorded.')
        f.append('  - DEADLINE of this product (latest date you may record for it): ' + say(deadline) + '.')
        f.append('  - Persuasion attempts allowed for this product: ' + str(2 if late else 1) + '.')
        f.append('  - Attempt 1: propose ' + money(full) + ' (or the floor ' + money(floor) + ' if the customer already said the full amount of this product is too much) on ' + say(pa1) + '.')
        if late:
            f.append('  - Attempt 2: propose the floor ' + money(floor) + ' on the deadline, ' + say(deadline) + '.')
        else:
            f.append('  - No attempt 2 for this product. If attempt 1 is refused, this product ends with no promise (note reason refused).')
    vars['prod_ids'] = ','.join(ids)
    vars['prod_names'] = '; '.join(names)
    vars['n_products'] = str(len(items))
    vars['debt_facts'] = '\n'.join(f)
    if len(items) == 1:
        q = 'Can you make this payment today?'
        intro = 'I am calling from the Freya Bank collections team about a late payment.'
    else:
        q = 'Can you pay all of them today?'
        intro = 'I am calling from the Freya Bank collections team about late payments on ' + str(len(items)) + ' products.'
    vars['debt_statement'] = 'Thank you, ' + title + ' ' + last + ', you are verified. ' + intro + ' ' + ' '.join(sentences) + ' ' + q
    maxd = max(x[0] for x in deadlines)
    cal = []
    d = today
    while d <= maxd:
        tag = ''
        dls = [x[2] + ' (' + x[1] + ')' for x in deadlines if x[0] == d]
        if dls:
            tag = ' <-- DEADLINE of ' + ', '.join(dls)
        if isb(d):
            cal.append('- ' + say(d) + (' (today)' if d == today else '') + ': business day.' + tag)
        else:
            why = 'weekend' if d.weekday() >= 5 else 'bank holiday'
            cal.append('- ' + say(d) + ': ' + why + ', NOT allowed. Offer the next business day, ' + say(nextb(d)) + ', instead.' + tag)
        d = d + D1
    cal.append('- A business day is allowed for a product only if it is not after THAT product\'s deadline. For a date after a product\'s deadline: say the latest date you can record for it is its deadline and offer the deadline.')
    cal.append('- Every date before today is in the past and not allowed.')
    vars['calendar_facts'] = '\n'.join(cal)
    vars['account_state'] = 'loaded'
try:
    _facts()
except Exception as e:
    vars['account_state'] = 'error'
    for k in ['today_text', 'callback_facts', 'weekday_facts', 'debt_facts', 'calendar_facts', 'debt_statement', 'prod_names']:
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

PID_FIX = r"""        ids = [x for x in str(vars.get('prod_ids') or '').split(',') if x]
        pid = str(b.get('product_id') or '').strip()
        if ids and pid not in ids:
            low = pid.lower()
            names = str(vars.get('prod_names') or '').split('; ')
            hit = ''
            for nm in names:
                parts = nm.split(' = ', 1)
                if len(parts) == 2 and low and (low in parts[1].lower() or parts[1].lower() in low):
                    hit = parts[0]
            if not hit:
                digits = ''.join(ch for ch in pid if ch.isdigit())
                for i in ids:
                    if digits and ''.join(ch for ch in i if ch.isdigit()) == digits:
                        hit = i
            if not hit and len(ids) == 1:
                hit = ids[0]
            if hit:
                pid = hit
        b['product_id'] = pid
        if vars.get('customer_id'):
            b['customer_id'] = str(vars.get('customer_id'))
"""

STATUS = r"""    def _status():
        ids = [x for x in str(vars.get('prod_ids') or '').split(',') if x]
        prom = [x for x in str(vars.get('promised_ids') or '').split(',') if x]
        notes = {}
        for kv in str(vars.get('note_map') or '').split(','):
            if ':' in kv:
                notes[kv.split(':')[0]] = kv.split(':')[1]
        open_ids = [i for i in ids if i not in prom and i not in notes]
        return ids, prom, notes, open_ids
"""

PROMISE_PRE = r'''try:
    b = config.get('body')
    if isinstance(b, dict):
''' + PID_FIX + r'''        vars['last_promise_pid'] = pid
        try:
            b['amount'] = round(float(str(b.get('amount')).replace(',', '')), 2)
        except Exception:
            pass
        b['date'] = str(b.get('date') or '')[:10]
except Exception:
    pass
'''

PROMISE_POST = r'''try:
''' + STATUS + r'''    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = {'ok': False}
            result['data'] = d
        pid = str(vars.get('last_promise_pid') or '')
        if str(d.get('ok')).lower() == 'true':
            prom = [x for x in str(vars.get('promised_ids') or '').split(',') if x]
            if pid and pid not in prom:
                prom.append(pid)
            vars['promised_ids'] = ','.join(prom)
            vars['promise_done'] = 'yes'
            ids, prom, notes, open_ids = _status()
            d['what_to_do_next'] = ('The promise for ' + pid + ' IS recorded. Recorded so far: ' + ', '.join(prom) + '. '
                'If the customer confirmed a promise for another product that is not recorded yet, call create_promise for it now, nothing else. '
                'When every confirmed promise is recorded: tell the customer the promises are recorded and say exactly "You will receive a text message confirming this promise." '
                'Products with neither a promise nor a note: ' + (', '.join(open_ids) if open_ids else 'none') + '. Unless the call is being transferred, each of them needs add_note (one call per message) before you say goodbye and call close_call with outcome promise and no reason.')
        else:
            n = 0
            try:
                n = int(vars.get('promise_fails_' + pid) or 0) + 1
                vars['promise_fails_' + pid] = str(n)
                vars['promise_fails'] = str(max(n, int(vars.get('promise_fails') or 0)))
            except Exception:
                n = 0
            if n <= 1:
                d['what_to_do_next'] = 'The promise for ' + pid + ' was NOT recorded (service error). Do NOT say it is recorded. Call create_promise again now, once, for the same product with exactly the same amount and date.'
            else:
                d['what_to_do_next'] = ('The promise for ' + pid + ' was NOT recorded again. Do not try it again. Tell the customer you could not record the promise for this product. '
                    'Record any other confirmed promises that are not recorded yet. Then call add_note for ' + pid + ' with reason service_error, and add_note for every other product without a promise, then thank, say goodbye and call close_call.')
except Exception:
    pass
'''

NOTE_PRE = r'''try:
    b = config.get('body')
    if isinstance(b, dict):
''' + PID_FIX + r'''        vars['last_note_pid'] = pid
        vars['last_note_reason'] = str(b.get('reason') or '')
except Exception:
    pass
'''

NOTE_POST = r'''try:
''' + STATUS + r'''    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = {'ok': False}
            result['data'] = d
        pid = str(vars.get('last_note_pid') or '')
        if str(d.get('ok')).lower() == 'true':
            ids, prom, notes, open_ids = _status()
            if pid and pid not in notes:
                notes[pid] = str(vars.get('last_note_reason') or '')
                vars['note_map'] = ','.join(k + ':' + v for k, v in notes.items())
            ids, prom, notes, open_ids = _status()
            if open_ids:
                d['what_to_do_next'] = 'Note saved for ' + pid + '. Products that still need add_note: ' + ', '.join(open_ids) + '. Call add_note for the next one now. Say nothing about notes to the customer.'
            else:
                if prom:
                    oc = 'outcome promise and no reason'
                elif ids and all(notes.get(i) == 'paid' for i in ids):
                    oc = 'outcome paid and no reason'
                else:
                    rs = [notes.get(i) for i in ids if notes.get(i) and notes.get(i) != 'paid']
                    oc = 'outcome no_promise and reason ' + (rs[0] if rs else 'refused')
                d['what_to_do_next'] = 'Note saved. Every product is handled. Do not mention notes. Now thank the customer, say goodbye, and in the same message call close_call with ' + oc + '.'
        else:
            d['what_to_do_next'] = 'The note was not saved. Call add_note once more with the same product_id and reason.'
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
                d['what_to_do_next'] = 'The transfer is done. In ONE message: tell the customer you are connecting them now, thank them, say goodbye, and call close_call with outcome transferred and the matching reason (restructuring queue: restructuring; general queue: human; support queue: support). Do NOT call add_note and do NOT call create_promise.'
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
        ids = [x for x in str(v.get('prod_ids') or '').split(',') if x]
        prom = [x for x in str(v.get('promised_ids') or '').split(',') if x]
        notes = {}
        for kv in str(v.get('note_map') or '').split(','):
            if ':' in kv:
                notes[kv.split(':')[0]] = kv.split(':')[1]
        promised = bool(prom) or v.get('promise_done') == 'yes'
        verified = tb(v.get('id_all_verified')) or tb(v.get('verify_identity_response.data.all_verified')) or v.get('account_state') == 'loaded'
        left = str(v.get('id_left') if v.get('id_left') is not None else v.get('verify_identity_response.data.wrong_answers_left'))
        fails = num(v.get('promise_fails'))
        if q in RM and (tok is None or tb(tok)):
            o, r = 'transferred', RM[q]
        elif promised:
            o, r = 'promise', ''
        elif notes and ids and all(notes.get(i) == 'paid' for i in ids):
            o, r = 'paid', ''
        elif notes and ids and any(notes.get(i) and notes.get(i) != 'paid' for i in ids):
            for i in ids:
                if notes.get(i) and notes.get(i) != 'paid':
                    o, r = 'no_promise', notes.get(i)
                    break
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
        required=["outcome"])  # no hooks: a failed pre-hook in the sandbox blocks close_call entirely (seen in run 2)
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
    "product_id": ("{{promise_product}}", "The product_id of this product exactly as listed in the facts (for example p1). One call per product."),
    "amount": ("{{promise_amount}}", "The agreed amount for this product, a number with up to 2 decimals, e.g. 637.5."),
    "date": ("{{promise_date}}", "The agreed payment date for this product as YYYY-MM-DD, taken from the calendar."),
}, "Record the payment promise for ONE product. CALL it only after the customer clearly said yes to your read-back of all promises. Call it once per promised product, one call per message.",
    required=["product_id", "amount", "date"], mapping=[("data.promise_id", "promise_id"), ("data.ok", "promise_ok")], pre=PROMISE_PRE, post=PROMISE_POST)

note = api_tool("add_note", {
    "customer_id": ("{{customer_id}}", ""),
    "product_id": ("{{note_product}}", "The product_id exactly as listed in the facts (for example p2)."),
    "reason": ("{{note_reason}}", "Why this product ended without a promise: refused, hardship, dispute, paid or service_error."),
}, "Record why ONE product ended without a promise. CALL it at the end of the call, once for each product that has no recorded promise, one call per message. NEVER call it if the call is being transferred.",
    required=["product_id", "reason"], pre=NOTE_PRE, post=NOTE_POST)

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
STRICT PRIVACY: Do not say why you are calling. Say nothing beyond: you are Freya Bank's digital assistant and we record this call. Never mention collections, a debt, a payment, an amount, an account or a product, even if they ask or offer to pay. Do not ask or accept security answers from them. If they ask what it is about or offer to pay, say you can only discuss it with {{title}} {{last_name}} personally.
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

COLLECTION = """The customer is verified. You have just stated every late product and asked whether they can pay today (with several products: whether they can pay all of them today). Get a payment promise for each product where you can, following these rules exactly, then close the call. Tools you may use here: create_promise, add_note, transfer_to_agent, close_call.

FACTS FOR THIS CALL (computed; trust them, never compute dates or amounts yourself). Products in the order you must handle them:
{{debt_facts}}

CALENDAR FROM TODAY TO THE LATEST DEADLINE (look up every day the customer names here):
{{calendar_facts}}
Weekday names:
{{weekday_facts}}
"Next <weekday>" means that weekday in the following calendar week. "This <weekday>" means the one in the current week.

SPECIAL SITUATIONS (check these first; they come before any persuasion)
- Illness, a death in the family or job loss: say ONE short sentence of sympathy. Make no persuasion attempt. Offer to connect them with the customer support team. If they accept: call transfer_to_agent with queue support. If they decline: add_note with reason hardship for every product without a recorded promise, then thank, goodbye, close_call no_promise / hardship. If they still name a date or an amount themselves, handle it as a promise below.
- They want a new payment plan or lower instalments: describe no plan; call transfer_to_agent with queue restructuring right away. Promises already recorded stay; promises agreed but not yet recorded are NOT recorded.
- They say they already paid a product's past-due amount: say that payments can take one business day to appear and take no promise for that product (it gets note reason paid). With other products left, continue with them.
- "This debt is not mine", "I never had this product", or a product was closed or paid off in the past: do not argue and take no promise for that product (it gets note reason dispute). With other products left, continue with them.
- They want a human: ask once whether you can help first. If they insist: transfer_to_agent with queue general.
- They say twice that they do not want to talk: stop; every product without a promise gets note reason refused; then close.
- Never offer a discount, a waiver, an instalment plan or any term that is not written here. If asked, say you cannot offer that.
- TRANSFERS: on any transfer never call add_note, before or after it.

ORDER OF THE CONVERSATION
- The question about paying today was already asked once for everything. Never ask about today again for each product.
- If they can pay everything today: agree the full amounts today for every product and go to CONFIRM AND RECORD.
- If they name one date (and no amount) for everything: use it for every product with its full amount, checking the date per product: a product whose deadline is earlier than that date gets its own deadline instead, and you say so. Then go to CONFIRM AND RECORD.
- Otherwise go through the products ONE BY ONE, in the order of the facts. For each product, start at attempt 1 of that product. Name the product each time.
- When a product is agreed during this round, only acknowledge it in a few words and move to the next product. Do NOT read it back and do NOT ask "Do you confirm?" or "Do you agree?" yet: there is exactly one read-back and one confirmation, at the end, for all products together.
- The total number of offers you make for one product after a flat refusal equals its allowed persuasion attempts. Count them.

AMOUNTS (per product)
- You propose every amount. NEVER ask the customer for an amount in any form: not "how much can you pay?", not "which amount?". When you ask a question, ask only about the day, and always name the amount yourself. If the customer asks you to suggest, propose per these rules.
- Ask for the full past-due amount first.
- A plain "no", "I am not paying it" or "I won't pay" is a FLAT REFUSAL, not "too much": answer it only with the next attempt from the facts (PERSUASION). Never offer the floor in reply to a flat refusal, except as attempt 2.
- ONLY if the customer explicitly says the full amount of a product is too much or more than they can afford: offer the floor of that product once, together with the attempt-1 date of that product, and say that the rest stays overdue. Example: "I can record <floor> instead; the rest stays overdue. Can you pay <floor> by <attempt-1 date>?" That one offer is the floor offer AND attempt 1. Do not name the floor again except in attempt 2.
- An amount the customer proposes from the floor up to the full amount, both included, is accepted.
- Below the floor: say it cannot be recorded and offer the floor once. If they do not accept the floor, that is a flat refusal.
- Refusing the floor is a flat refusal.

DATES (per product)
- A promise date must be today or later, a business day, and not after THAT product's deadline. Check it in the calendar.
- A weekend day or holiday that is not after the deadline: offer the next business day given in the calendar.
- A date after the product's deadline: say the latest date you can record for that product is its deadline and offer the deadline. If they refuse the deadline, that is a flat refusal.
- A vague period ("next week", "end of the month", "when my salary comes"): ask ONCE for the exact day. If they still give no day, propose the deadline.

PERSUASION (counted per product)
- A date or an amount the customer proposes is never a refusal. Handle it with AMOUNTS and DATES.
- A flat refusal or a non-answer uses one persuasion attempt of the current product. A "no" to paying today with no date and no amount is a flat refusal.
- Use that product's attempt offers from the facts, in order, and only as many as allowed. Never repeat an offer the customer already refused.
- When a product's attempts are used up: stop for that product (it gets note reason refused) and move to the next product.

CONFIRM AND RECORD
- When every product has either an agreed amount and date or has ended without a promise, read back ALL agreed promises together in one message: for each, the product, the amount and the date (weekday, day and month), mentioning any date moved to a product's deadline. Then ask "Do you confirm?". Ask for one confirmation only.
- After a clear yes, call create_promise once per agreed product, one call per message, with that product's product_id, amount and date. Do not speak between these calls except to report a failure.
- If they change a date or an amount at the read-back, check it again and read back again.
- Never say a promise is recorded before create_promise returned ok true. Follow what_to_do_next in each tool result.
- IF create_promise FAILS for a product (an error, "service unavailable", or ok false), nothing was recorded for it. Call create_promise AGAIN for that product right away, exactly once more, with the same values. If the second call also fails: tell the customer you could not record that promise; keep the others; that product gets note reason service_error.
- After the promises are recorded say exactly: "You will receive a text message confirming this promise." One promise per product; a recorded promise cannot be changed in this call (they can change it in the mobile app).

NOTES AND CLOSING (not on a transfer)
- Before saying goodbye, call add_note once for EVERY product without a recorded promise, one call per message, with its product_id and its reason: refused, hardship, dispute, paid or service_error. This applies also when no product got a promise and when there is only one product. Do not mention notes to the customer.
- Then thank the customer, say goodbye, and in the same message call close_call:
  - promise (no reason): at least one promise was recorded.
  - paid (no reason): every product was already paid.
  - transferred: restructuring, human or support, after transfer_to_agent.
  - no_promise with the reason of the first product (in the order of the facts) whose note is not paid."""

nodes = {
    "setup": code_node("setup", "opening"),
    "opening": conv("opening", OPENING, [close_tool()], [
        CLOSED,
        {"target_node": "identity", "description": "The person confirmed they are {{title}} {{last_name}}",
         "condition": {"operator": "matches_intent", "description": "In the last message the person clearly confirms they are {{title}} {{first_name}} {{last_name}}: they say yes, that's me, speaking, or say their own name. A greeting such as hello or go ahead, or a question such as who is this, is NOT a confirmation."}},
        {"target_node": "third_party", "description": "Someone else answered, or wrong number, or the customer died",
         "condition": {"operator": "matches_intent", "description": "The person says they are NOT {{first_name}} {{last_name}} (for example a relative, a spouse or a colleague answering), or that the customer is not available or not at home, or that this number does not belong to the customer, or that the customer has died."}},
    ], on_enter={"type": "tts", "text": "Hello, this is Freya Bank's digital assistant calling. Please note that we record this call. Am I speaking with {{title}} {{last_name}}?"}),
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
    "collection": conv("collection", COLLECTION, [promise, note, transfer, close_tool()], [CLOSED], turns=80,
                       on_enter={"type": "tts", "text": "{{debt_statement}}"}),
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
