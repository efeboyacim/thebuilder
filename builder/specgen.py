"""Stages 3-6: brief material -> digest -> spec, validated and repaired.

1. Digest (one model call, structured output): every rule of the material, numbered, with a verbatim quote; the
   amendment log, explicit holidays, the code list, and the sentences that are not rules. Code then renumbers the
   rules, checks every quote against the material and measures how much of the material the digest covers.
2. Spec (one model call): the spec schema filled from the digest, the material and the tool API summary.
3. Validate and repair (at most REPAIR_ROUNDS model calls): the validator's errors go back as a patch request.

The prompts describe the schema in generic terms; no value of any brief is written here.
"""
import json, os, re, time

from builder import spec as sp
from builder import bindings as bd
from builder import toolapi
from builder.llm import LLM, LLMError

REPAIR_ROUNDS = 2
DIGEST_TIMEOUT = 600
SPEC_TIMEOUT = 600
TOPICS = ("call_variables", "opening", "third_party", "identity", "disclosure", "calendar", "dates", "amounts",
          "negotiation", "confirmation", "recording", "events", "side_questions", "notes", "multi_product", "closing",
          "tools", "other")

DIGEST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["sources", "rules", "amendments", "holidays", "codes", "skipped"],
    "properties": {
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "file", "section", "kind", "relevance", "relevance_quote"],
            "properties": {"id": {"type": "string"}, "file": {"type": "string"}, "section": {"type": "string"},
                           "kind": {"type": "string", "enum": ["spec", "mail", "policy", "memo", "code_list", "other"]},
                           "relevance": {"type": "string", "enum": ["binding", "ignore", "wish", "background"]},
                           "relevance_quote": {"type": "string"}}}},
        "rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "source", "topic", "quote", "note", "status", "replaced_by"],
            "properties": {"id": {"type": "string"}, "source": {"type": "string"},
                           "topic": {"type": "string", "enum": list(TOPICS)},
                           "quote": {"type": "string"}, "note": {"type": "string"},
                           "status": {"type": "string", "enum": ["binding", "replaced", "wish"]},
                           "replaced_by": {"type": "string"}}}},
        "amendments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["target", "by", "before", "after", "effective_from"],
            "properties": {"target": {"type": "string"}, "by": {"type": "string"}, "before": {"type": "string"},
                           "after": {"type": "string"}, "effective_from": {"type": "string"}}}},
        "holidays": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["date", "name", "quote"],
            "properties": {"date": {"type": "string"}, "name": {"type": "string"}, "quote": {"type": "string"}}}},
        "codes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["code", "meaning", "used_for", "back_office_only"],
            "properties": {"code": {"type": "string"}, "meaning": {"type": "string"},
                           "used_for": {"type": "string", "enum": ["outcome", "reason", "queue", "note_reason", "hint",
                                                                    "question", "product_kind", "tool", "field", "other"]},
                           "back_office_only": {"type": "boolean"}}}},
        "skipped": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["quote", "why"],
            "properties": {"quote": {"type": "string"}, "why": {"type": "string"}}}},
    },
}

DIGEST_PROMPT = """You read the material a bank wrote for an automated phone agent that calls customers about late
payments. Turn it into one consolidated, numbered rule list. A later step builds the agent only from your list:
every rule you skip will be missing from the agent, and builders fail most often by reading too shallowly. Read every
sentence, list item and table row.

The material is one or more files, given in order. A later file or a later section (for example a memo) may amend,
replace or withdraw earlier text, and one file may say that every rule of an earlier one applies unless replaced.
When text is amended: the earlier rule gets status "replaced" and replaced_by the id of the rule that replaces it,
and an amendments entry records the before and after quotes. A rule that applies only from a date keeps that date in
effective_from (YYYY-MM-DD); otherwise effective_from is empty. Withdrawn text becomes status "replaced" with an empty
replaced_by.

Fields:
- sources: one entry per section (each heading of level 1 or 2; a whole file when it has no headings). id S1, S2, ...
  in material order; file is the file name; kind as the section presents itself; relevance: binding (rules the agent
  must follow), ignore (the material says to disregard it, or it concerns something other than these calls), wish
  (a suggestion, not a rule), background (context without rules); relevance_quote: the words that justify the label.
- rules: one entry per rule, ids R1, R2, ... in material order. A rule is one thing the agent must do, must not do,
  or must decide, including definitions the rules depend on (call variables, tables, calendars, code tables). Keep a
  numbered rule of the material as one entry unless it sets clearly separate behaviours; one entry per table row,
  plus one for the table's header sentence. Each persuasion step, each band and each date rule is its own rule.
  - quote: copied verbatim from the material, with its punctuation. For a table row: the cells separated by " | ".
  - note: a self-contained restatement when the quote alone is not clear (a table row with its column meanings, a
    reference to another rule, an amended value, which rule it overrides). Empty when the quote is clear.
  - status: binding, replaced or wish. Rules from an ignored section are not listed; put them in skipped.
- holidays: every non-business date as an explicit date. A holiday given only by name becomes its dates for the year
  of the material and the next two years.
- codes: every code value the material or the tool API uses (outcomes, reasons, queues, note reasons, hint values,
  question names, product kinds, tool names), with its meaning and use; back_office_only when the material says the
  agent must not use it.
- skipped: every sentence of the material that is not part of a rule (titles, introductions, background, ignored
  sections), with a short reason. Rules and skipped together cover the whole material.

The tool API the agent will use (for code values only; its descriptions are not rules of the material):
{tool_api}

The material:
{material}
"""

SPEC_PROMPT = """You write the spec of an automated phone agent that calls a bank's customers about late payments.
A generic policy engine runs the call from your spec: code decides every step, count, date and tool call, and speaks
your templates word for word. The engine knows no bank: everything bank-specific comes from your spec, and every
value must come from the material, its digest or the tool API below.

How the engine runs a call:
- Stages: open (the first words; find out who is on the line), third (someone other than the customer is on the
  line), identity (security questions, one tool call per answer), negotiate (after verification: the lookup tool,
  the disclosure, the first ask and anything after it), confirm (read-back, confirmation, recording), close.
- Before identity is verified, nobody hears anything about the account or the reason for the call.
- Every call ends in exactly one situation. The engine calls the close tool last, with the situation's outcome and
  reason, after saying the situation's say text and farewell.

Rules for the spec:
1. Output one JSON object that matches the JSON Schema below. Every listed field is required.
2. A switch the material does not settle is the string "not_specified". Never fill it from habit, from another bank
   or from what seems sensible; "not_specified" is a valid, expected answer.
3. Templates are the agent's exact words, in the material's language. Use the material's own sentence wherever it
   prescribes one, word for word. Placeholders are {{{{name}}}}: call-variable names everywhere, plus the engine
   placeholders listed below for each template. Never put a value a placeholder can supply into a template.
4. Codes (outcomes, reasons, queues, note reasons, question names, hint values, product kinds) are copied exactly from
   the tool API's enums, or from the material when the API has no enum. Never invent one.
5. situations: every way a call can end, at least one per (outcome, reason) pair of the outcome table, plus separate
   entries when the words said at the end differ. reason is "" when the outcome takes none; queue is the transfer
   queue for a situation that hands the call over, else ""; note_reason is the note reason a product gets in that
   situation when the material uses notes, else "".
6. Rules the sections cannot express go to residual_rules: class speech (wording or tone only) or flow (changes a
   tool call, an outcome, an amount, a date or a count; give its trigger). Dates and the calendar, bands and
   deadlines, the floor, amounts, the persuasion ladder, tool failures, events and side questions have their own
   sections (calendar, products, negotiation, failure_policy, events, side_questions). Notes and rules for several
   products belong in residual_rules in this spec version: put each one there, complete and with every value.
7. rule_index: every digest rule with status binding appears exactly once, with the spec paths that carry it and/or
   residual:<id>. Paths are dotted, list items by [index] or [id] (situations[some_id], identity.questions[0]).
8. refs: the ids of the digest rules a part comes from.
9. phrases: every sentence the material prescribes word for word, verbatim, with the template paths that contain it.
10. opening.forbidden_before_verification: the words the material forbids before verification, plus the spoken name
   of every product kind when it forbids naming the product. No template spoken before verification (opening,
   third_party, identity, and the situations those stages reach) may contain one of them.
11. Dates: the engine resolves every day a customer names against the call's current date and checks every date it
   would record or offer against the calendar and the product's deadline (today or later, a business day, not after
   the deadline), then applies negotiation.non_business_day or negotiation.after_deadline. Ladder dates are kept
   inside that range by the engine; write the expressions as the material states them.
12. negotiation.ladder: one step per persuasion attempt the material describes, in order. A step applies to the bands
   it lists (empty = every band); a product gets at most its band's attempts. The first ask is not an attempt.
13. events: one entry per thing a customer may say that changes the call, in the material's order of precedence
   (a rule that one comes before another puts it first, and adds the other event's id to its suppresses; suppresses
   also lists persuasion when no persuasion attempt may follow it). A rule that applies before
   and after verification with different outcomes is one entry with then and then_before_verification. A request that
   applies only when repeated, with a question from the agent in between, is min_occurrences 2 with
   below_min_template. A yes/no offer (for example a transfer) is template + on_yes + on_no.
14. side_questions: answers for the questions the material gives a fixed answer to; other for the rest.
15. Honesty wording: a judge reads the transcript literally. No template spoken before a bank write succeeded (the
   opening, identity, disclosure, offers, read-back) may use the word the material uses for a successful write (the
   verb in confirm.recorded_template), not even with another meaning: when the material requires such content (for
   example about the call itself), keep the meaning with another form of the word, such as its -ing form.

Engine placeholders per template (besides call-variable names):
{templates}

Expressions (named periods, callback day rule, band selectors and deadlines, the floor, first-ask and ladder
amounts and dates) use a small Python subset: names, numbers, strings,
comparisons, and/or/not, + - * /, conditional expressions, and calls to these helpers only:
  is_business_day(d), add_business_days(d, n), add_calendar_days(d, n), weekday(d) (0 = Monday), roll(d, 'next'|'prev'),
  last_bd_of_week(d, offset_weeks), last_bd_of_month(d), min, max, abs, ceil_to(x, step), round_half_up(x, places).
Names per expression:
{expressions}
Business days come from the calendar section. Examples: a day rule d >= today and d <= add_business_days(today, 3)
and is_business_day(d); a band selector days_overdue >= 31 and days_overdue <= 60; a deadline
add_calendar_days(today, 10); a ladder amount max(floor, due_amount / 2); a floor expression may test kind and read
fields of the product row by their names.

Bindings map the engine's roles onto this tool API.
{binding_roles}
Example from an unrelated domain (a library): tools {{"lookup": "list_loans"}}; request {{"lookup": {{"member_ref":
{{"source": "call_var", "value": "customer_key"}}}}, "record_promise": {{"loan": {{"source": "engine", "value":
"product_ref"}}, "fee_cents": {{"source": "engine", "value": "amount", "convert": "cents_int"}}, "return_on":
{{"source": "engine", "value": "date", "convert": "date_iso"}}, "channel": {{"source": "literal", "value": "phone"}}}}}};
response {{"lookup": {{"ok": "success", "products": "loans", "item": {{"ref": "loan_no", "kind": "medium",
"due_amount": "fee", "days_overdue": "late_days"}}}}}}.

JSON Schema of the spec:
{schema}

Tool API:
{tool_api}

Digest of the material (the rules to index; status replaced or wish needs no index entry):
{digest}

The material itself (the authority for exact wording):
{material}

Answer with the JSON object only.
"""

REPAIR_PROMPT = """The spec below failed validation. Fix every error with the smallest change that makes the spec true
to the material. Do not weaken a rule to pass a check.

Answer with one JSON object only: {{"patches": [...]}}. Each patch is one of
  {{"op": "set", "path": "<path>", "value": <JSON value>}}   replaces or adds the value at path
  {{"op": "remove", "path": "<path>"}}
  {{"op": "append", "path": "<path of a list>", "value": <JSON value>}}
Paths are dotted, list items by [index] or [id] (situations[some_id], identity.questions[0].field). Setting a whole
object or list at once is fine.

Errors:
{errors}

The spec:
{spec}

Context, as given to the spec writer:
{context}
"""


class SpecGenError(Exception):
    pass


# ---------- material and digest ----------

def material_text(briefs):
    parts = []
    for n, b in enumerate(briefs, 1):
        parts.append(f"===== file {n} of {len(briefs)}: {os.path.basename(b.path)} =====\n{b.text.strip()}\n")
    return "\n".join(parts)


def renumber(digest):
    """Rule ids R1..Rn in list order; references in replaced_by and amendments follow."""
    rules = [r for r in digest.get("rules") or [] if isinstance(r, dict)]
    old_to_new = {}
    for i, r in enumerate(rules, 1):
        old_to_new.setdefault(str(r.get("id")), f"R{i}")
        r["id"] = f"R{i}"
    for r in rules:
        if r.get("replaced_by"):
            r["replaced_by"] = old_to_new.get(r["replaced_by"], r["replaced_by"])
    for a in digest.get("amendments") or []:
        for k in ("target", "by"):
            if a.get(k):
                a[k] = old_to_new.get(a[k], a[k])
    digest["rules"] = rules
    return digest


def binding_rule_ids(digest):
    return [r["id"] for r in digest.get("rules") or [] if r.get("status") == "binding"]


_ROW_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")


def statements(text):
    """The material as statements: table rows, and the sentences of every other line."""
    out = []
    for line in text.splitlines():
        if not line.strip() or _ROW_SEP.match(line):
            continue
        if line.strip().startswith("|"):
            out.append(line.strip())
            continue
        body = re.sub(r"^\s*(#+|[-*]|\d+\.)\s*", "", line)
        out += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(`])", body) if s.strip()]
    return [s for s in out if len(re.findall(r"[A-Za-z0-9]+", s)) >= 3]


def _words(s):
    return set(re.findall(r"[a-z0-9]+", sp._norm(s)))


def check_digest(digest, material, texts=None):
    """Quotes found verbatim in the material, and how much of the material the rules and skips cover. texts: the
    brief texts whose statements are counted (default: the material as one text)."""
    norm_mat = sp._norm(material)
    quotes = [(r.get("id"), r.get("quote") or "") for r in digest.get("rules") or []]
    unverified = [rid for rid, q in quotes if not _quote_found(q, norm_mat)]
    covers = [sp._norm(q) for _, q in quotes] + [sp._norm(s.get("quote") or "") for s in digest.get("skipped") or []]
    covers = [c for c in covers if c]
    cover_words = [_words(c) for c in covers]
    stmts = [st for t in (texts if texts is not None else [material]) for st in statements(t)]
    uncovered = []
    for s in stmts:
        ns, ws = sp._norm(s), _words(s)
        if any(ns in c or (c in ns and len(c) >= 0.5 * len(ns)) for c in covers):
            continue
        if ws and any(len(ws & cw) >= 0.8 * len(ws) for cw in cover_words):
            continue
        uncovered.append(s.strip()[:160])
    return {"rules": len(quotes), "binding": len(binding_rule_ids(digest)),
            "quotes_verbatim": len(quotes) - len(unverified), "unverified_quotes": unverified,
            "statements": len(stmts), "covered": len(stmts) - len(uncovered), "uncovered": uncovered}


def _quote_found(quote, norm_mat):
    parts = [sp._norm(p) for p in quote.split(" | ")] if " | " in quote else [sp._norm(quote)]
    parts = [p.strip(" .") for p in parts if p.strip(" .")]
    return bool(parts) and all(p in norm_mat for p in parts)


def digest_markdown(digest, checks):
    lines = ["# Digest", "", f"{checks['rules']} rules ({checks['binding']} binding), "
             f"{checks['quotes_verbatim']} quotes verbatim, material coverage {checks['covered']}/{checks['statements']}.", ""]
    for s in digest.get("sources") or []:
        lines.append(f"- {s.get('id')} {s.get('file')} / {s.get('section')}: {s.get('kind')}, {s.get('relevance')}")
    lines += ["", "## Rules", ""]
    for r in digest.get("rules") or []:
        extra = f" (replaced by {r['replaced_by']})" if r.get("replaced_by") else ""
        lines.append(f"- **{r['id']}** [{r.get('topic')}, {r.get('status')}{extra}] {r.get('quote')}")
        if r.get("note"):
            lines.append(f"  - note: {r['note']}")
    for key, title in (("amendments", "Amendments"), ("holidays", "Holidays"), ("codes", "Codes"), ("skipped", "Skipped")):
        rows = digest.get(key) or []
        if rows:
            lines += ["", f"## {title}", ""] + [f"- {json.dumps(x, ensure_ascii=False)}" for x in rows]
    if checks["uncovered"]:
        lines += ["", "## Material not covered", ""] + [f"- {u}" for u in checks["uncovered"]]
    return "\n".join(lines) + "\n"


def digest_for_prompt(digest):
    rules = [{k: r[k] for k in ("id", "topic", "status", "replaced_by", "quote", "note") if r.get(k)}
             for r in digest.get("rules") or []]
    return json.dumps({"rules": rules, "amendments": digest.get("amendments") or [],
                       "holidays": digest.get("holidays") or [], "codes": digest.get("codes") or []},
                      ensure_ascii=False, indent=0)


# ---------- prompt parts generated from the engine's constants ----------

def templates_text():
    lines = []
    for path, extra in sp.TEMPLATES.items():
        names = [("<any field of the lookup tool's product row>" if x == sp.ITEM_FIELDS else x) for x in extra]
        lines.append(f"  {path}: {', '.join(names) if names else 'none'}")
    lines.append("  where product_name is the product's name from disclosure.product_names, due_amount the amount overdue,"
                 " days_overdue the days late, amount/date the agreed or proposed values, items the read-back lines, "
                 "question the confirmation question, product_count the number of products.")
    return "\n".join(lines)


def expressions_text():
    lines = []
    returns = {"bool": "true or false", "amount": "a number of 0 or more", "date": "a date"}
    for path, names in sp.EXPRESSIONS.items():
        shown = [("<any field of the lookup tool's product row>" if x == sp.ITEM_FIELDS else x) for x in names]
        lines.append(f"  {path}: {', '.join(shown)}; returns {returns[sp.EXPR_TYPES[path]]}")
    lines.append("  (today and d are dates, now a date-time; ref, kind, due_amount, days_overdue are the product's "
                 "bound item roles; floor is the product's floor from products.floor, deadline the latest date of its "
                 "band, said_too_much true once the customer said the full amount is too much or proposed less.)")
    return "\n".join(lines)


def binding_roles_text():
    lines = ['Format: {"call_vars": {call-variable role: variable name}, "tools": {tool role: tool name}, "request": '
             '{tool role: {body field path: {"source": ..., "value": ..., "convert": ...}}}, "response": {tool role: '
             '{response role: response path, "item": {item role: path inside one item}}}}.',
             f"Call-variable roles: {', '.join(bd.CALL_VAR_ROLES)} (customer_key: the id passed to the tools; clock: the "
             "current date-time; given_name, family_name, honorific, company only when such a variable exists).",
             "Tool roles, with the engine roles a request can carry and the response roles it can read:"]
    for role, d in bd.TOOL_ROLES.items():
        item = f"; item roles inside one product: {', '.join(d['item'])}" if "item" in d else ""
        lines.append(f"  {role}: request {', '.join(d['request']) or '(none)'}; response "
                     f"{', '.join(bd.COMMON_RESPONSE + d['response'])}{item}")
    lines += [
        "  (verify: question = which security question, answer = the normalised answer; verified = this answer passed, "
        "fully_verified = every question passed, attempts_left = wrong answers left, hint = the tool's hint. lookup: "
        "products = the list of late products; item ref = product id, kind = product kind, due_amount = amount "
        "overdue, days_overdue = days late. record_promise: product_ref, amount, date; promise_ref = its id. "
        "record_note: product_ref, note_reason, note_text. book_callback: date, time. transfer: queue. close: "
        "outcome, reason.)",
        f"Field sources: {', '.join(bd.SOURCES)}. call_var takes a call-variable role; engine takes an engine role of "
        "that tool role (a list of two roles only for a joined field); literal a fixed value; template a string with "
        "{{role}} placeholders.",
        f"Converters (engine values only): {', '.join(bd.CONVERTERS)}. number_2dp: amount as a JSON number with two "
        "decimals; cents_int: amount in cents as an integer; string_2dp: amount as a string; date_iso: YYYY-MM-DD; "
        "time_hhmm: HH:MM; datetime_join: date and time joined into one field (value [\"date\", \"time\"]).",
        "Bind every required body field. Response paths are the field paths listed in the tool API; the product list's "
        "item paths are relative to one item. Bind ok and error when the response has them.",
    ]
    return "\n".join(x for x in lines if x)


def schema_text():
    return json.dumps(sp.SCHEMA, ensure_ascii=False, separators=(",", ":"))


def spec_context(digest, api, material):
    return dict(templates=templates_text(), expressions=expressions_text(), binding_roles=binding_roles_text(),
                schema=schema_text(), tool_api=toolapi.summary_text(api), digest=digest_for_prompt(digest),
                material=material)


# ---------- the stage ----------

def run(ctx, out_dir):
    """Digest, spec, validation and repair. Returns (spec, report, summary); raises SpecGenError when the spec stays
    invalid after the repair rounds."""
    trace, api, briefs = ctx["trace"], ctx["api"], ctx["inputs"].briefs
    llm = ctx.get("llm") or LLM(trace=trace)
    material = material_text(briefs)
    tool_api = toolapi.summary_text(api)
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.time()
    digest = llm.json(DIGEST_PROMPT.format(tool_api=tool_api, material=material),
                      purpose="digest: consolidate the brief material into numbered, quoted rules",
                      schema=DIGEST_SCHEMA, timeout=DIGEST_TIMEOUT)
    if not isinstance(digest, dict) or not digest.get("rules"):
        raise SpecGenError("the digest has no rules")
    digest = renumber(digest)
    dchecks = check_digest(digest, material, [b.text for b in briefs])
    _write(out_dir, "digest.json", json.dumps(digest, ensure_ascii=False, indent=1))
    _write(out_dir, "digest.md", digest_markdown(digest, dchecks))
    trace.event("check", name="digest", ok=not dchecks["unverified_quotes"] and not dchecks["uncovered"],
                seconds=round(time.time() - t0, 1),
                details={k: v for k, v in dchecks.items() if k not in ("uncovered",)},
                uncovered=dchecks["uncovered"][:30])
    for rid in dchecks["unverified_quotes"]:
        trace.warning(f"digest {rid}: quote not found verbatim in the material")
    rule_ids = binding_rule_ids(digest)
    known_ids = [r["id"] for r in digest["rules"]]

    context = spec_context(digest, api, material)
    t1 = time.time()
    spec = llm.json(SPEC_PROMPT.format(**context), purpose="spec: fill the spec schema from the digest and the tool API",
                    timeout=SPEC_TIMEOUT)
    brief_text = "\n".join(b.text for b in briefs)
    report = sp.validate(spec, api, brief_text, rule_ids, known_ids)
    _record(trace, 0, report, t1)
    rounds = 0
    while report.errors and rounds < REPAIR_ROUNDS:
        rounds += 1
        t2 = time.time()
        try:
            reply = llm.json(REPAIR_PROMPT.format(errors="\n".join(f"- {e}" for e in report.errors),
                                                  spec=json.dumps(spec, ensure_ascii=False, indent=1),
                                                  context=_repair_context(context)),
                             purpose=f"spec repair round {rounds}: fix {len(report.errors)} validation error(s)",
                             timeout=SPEC_TIMEOUT)
        except LLMError as e:
            trace.warning(f"spec repair round {rounds} failed: {e}")
            break
        patches = reply.get("patches") if isinstance(reply, dict) else None
        spec, perrs = sp.apply_patches(spec, patches or [])
        for e in perrs:
            trace.warning(f"spec repair round {rounds}: {e}")
        report = sp.validate(spec, api, brief_text, rule_ids, known_ids)
        _record(trace, rounds, report, t2, patches=len(patches or []))
    _write(out_dir, "spec.json", json.dumps(spec, ensure_ascii=False, indent=1))
    _write(out_dir, "spec_checks.json", json.dumps({"errors": report.errors, "warnings": report.warnings,
                                                     "info": report.info, "digest": dchecks}, ensure_ascii=False, indent=1))
    summary = spec_summary(digest, dchecks, report, rounds)
    if report.errors:
        raise SpecGenError(f"the spec still has {len(report.errors)} error(s) after {rounds} repair round(s); "
                           f"first: {report.errors[0]}")
    return spec, report, summary


def spec_summary(digest, dchecks, report, rounds):
    info = report.info
    return {"rules": dchecks["rules"], "binding_rules": dchecks["binding"],
            "coverage": info.get("rule_coverage"), "quotes_verbatim": dchecks["quotes_verbatim"],
            "material_coverage": f"{dchecks['covered']}/{dchecks['statements']}",
            "amendments": len(digest.get("amendments") or []), "holidays": len(digest.get("holidays") or []),
            "not_specified": info.get("not_specified"), "residuals": info.get("residuals"),
            "flow_residuals": info.get("flow_residuals"), "hangup_chosen_by_model": info.get("hangup_chosen_by_model"),
            "situations": info.get("situations"), "identity_questions": info.get("identity_questions"),
            "repair_rounds": rounds, "errors": len(report.errors), "warnings": len(report.warnings)}


def _repair_context(context):
    keep = ("templates", "expressions", "binding_roles", "schema", "tool_api", "digest")
    return "\n\n".join(f"[{k}]\n{context[k]}" for k in keep)


def _record(trace, rnd, report, t0, **extra):
    trace.event("check", name=f"spec_validate_{rnd}", ok=report.ok, seconds=round(time.time() - t0, 1),
                errors=report.errors[:60], n_errors=len(report.errors), warnings=report.warnings[:20], **extra)


def _write(out_dir, name, text):
    with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
