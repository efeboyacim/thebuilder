"""Expected ledgers for a mock brief, from a separate model pass over the brief text (never from our engine).

    python tools/expected_from_brief.py mocks/m1/        # reads <dir>/*.md, <dir>/scripts.json; writes <dir>/cases.json

scripts.json lists rehearsal cases without `expected` (format: tools/rehearse.py). Each case's customer facts (names,
identity, products in lookup order as p1..pN) come from its `base` case in public-test-set.json. One model call reads
the brief and every case and returns, per case, the ledger a perfect agent following only this brief would leave,
each product's band and deadline, and any check of ours it disagrees with. cases.json is the input of
tools/rehearse.py; a person reviews it before it is trusted. Imports nothing from builder/.
"""
import asyncio, datetime, json, os, pathlib, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = os.environ.get("EXPECTED_MODEL", "claude-opus-5-5")

LEDGER = {
    "type": "object", "additionalProperties": False,
    "required": ["outcome", "reason", "promises", "notes", "callbacks", "transfers"],
    "properties": {
        "outcome": {"type": "string"},
        "reason": {"type": ["string", "null"]},
        "promises": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["product_id", "amount", "date"],
                                                "properties": {"product_id": {"type": "string"},
                                                               "amount": {"type": "number"},
                                                               "date": {"type": "string"}}}},
        "notes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                             "required": ["product_id", "reason"],
                                             "properties": {"product_id": {"type": "string"},
                                                            "reason": {"type": "string"}}}},
        "callbacks": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["date", "time"],
                                                 "properties": {"date": {"type": "string"}, "time": {"type": "string"}}}},
        "transfers": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["queue"], "properties": {"queue": {"type": "string"}}}},
    },
}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["cases"],
    "properties": {"cases": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "deadlines", "expected", "check_issues"],
        "properties": {"id": {"type": "string"},
                       "deadlines": {"type": "array", "items": {
                           "type": "object", "additionalProperties": False,
                           "required": ["product_id", "band", "deadline"],
                           "properties": {"product_id": {"type": "string"}, "band": {"type": "string"},
                                          "deadline": {"type": "string"}}}},
                       "expected": LEDGER,
                       "check_issues": {"type": "array", "items": {"type": "string"}}}}}},
}

PROMPT = """You are the quality analyst of a bank. Below is the complete specification the bank gave its automated
phone agent for overdue-payment calls, and a list of test calls. For each call, work out exactly what a perfect agent
that follows ONLY this specification leaves in the bank's ledger.

For every call you get the call's date and time (`now`, with its weekday), the customer's facts as the bank's tools
return them (identity answers; products in the order the account tool returns them, as p1, p2, p3, with every field),
and what the customer will do and say. The customer follows the script; where the script leaves something open, the
customer goes along with what the agent says.

Ledger fields:
- outcome and reason: the values the agent sends to close_call (reason null when the outcome takes none);
- promises: one per create_promise that succeeds (product_id, amount with two decimals, date YYYY-MM-DD);
- notes: one per add_note (product_id, reason);
- callbacks: one per schedule_callback (date YYYY-MM-DD, time HH:MM);
- transfers: one per transfer_to_agent (queue).
A tool failure the case mentions (a failing first create_promise) leaves nothing in the ledger.

In `deadlines`, give each product's band and deadline (empty for a call that never reaches the account tool). Count
business days against the specification's calendar exactly. In `check_issues`, list every one of our checks for that
call that is wrong under the specification (empty when all are right).

Specification:
=====
{brief}
=====

Calls:
{cases}
"""


def weekday(now):
    return datetime.datetime.fromisoformat(now).strftime("%A")


def case_facts(case, pub):
    base = pub[case["base"]]
    v = dict(base["variables"], **(case.get("variables") or {}))
    products = [dict(product_id=f"p{i + 1}", **p) for i, p in enumerate(base["products"])]
    faults = base.get("faults") or []
    out = {"id": case["id"], "now": f"{v['now']} ({weekday(v['now'])})",
           "customer": {"first_name": v["first_name"], "last_name": v["last_name"], "title": v["title"]},
           "identity_answers": case.get("identity", base["identity"]), "products": products,
           "script": case["script"], "our_checks": case.get("checks", [])}
    if faults:
        out["tool_failures"] = [f"{f['tool']} fails on its first {f['fail_first_n']} call(s), then works" for f in faults]
    if base["family"] == "Third party" or case.get("third_party"):
        out["who_answers"] = "someone other than the customer"
    return out


async def ask(prompt):
    import claude_agent_sdk as sdk
    for k in [k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC"))] if os.environ.get("CLAUDECODE") else []:
        os.environ.pop(k, None)
    opts = sdk.ClaudeAgentOptions(model=MODEL, tools=[], allowed_tools=[], setting_sources=[], max_turns=4,
                                  cwd=tempfile.mkdtemp(prefix="expected-"), effort="high",
                                  output_format={"type": "json_schema", "schema": SCHEMA})
    result = None
    async for m in sdk.query(prompt=prompt, options=opts):
        if isinstance(m, sdk.ResultMessage):
            result = m
    if result is None or result.is_error:
        raise SystemExit(f"model call failed: {getattr(result, 'errors', None) or getattr(result, 'result', None)}")
    if result.structured_output is not None:
        return result.structured_output
    return json.loads(result.result)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    folder = pathlib.Path(sys.argv[1])
    briefs = sorted(folder.glob("*.md"))
    if len(briefs) != 1:
        raise SystemExit(f"expected one .md brief in {folder}, found {len(briefs)}")
    brief = briefs[0].read_text(encoding="utf-8")
    scripts = json.loads((folder / "scripts.json").read_text(encoding="utf-8"))
    pub = {c["id"]: c for c in json.loads((ROOT / "public-test-set.json").read_text(encoding="utf-8"))}
    facts = [case_facts(c, pub) for c in scripts]
    t0 = time.time()
    reply = asyncio.run(ask(PROMPT.format(brief=brief, cases=json.dumps(facts, ensure_ascii=False, indent=1))))
    got = {c["id"]: c for c in reply["cases"]}
    out, issues = [], 0
    for c in scripts:
        r = got.get(c["id"])
        if r is None:
            raise SystemExit(f"no answer for {c['id']}")
        exp = r["expected"]
        exp["promises"] = [dict(p, amount=round(float(p["amount"]), 2)) for p in exp["promises"]]
        out.append(dict(c, expected=exp, deadlines=r["deadlines"], check_issues=r["check_issues"]))
        issues += len(r["check_issues"])
    (folder / "cases.json").write_bytes((json.dumps(out, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print(f"{folder / 'cases.json'}: {len(out)} cases, {issues} check issue(s), {time.time() - t0:.0f} s, model {MODEL}")
    for c in out:
        e = c["expected"]
        print(f"  {c['id']}: {e['outcome']}/{e['reason']} promises={[(p['product_id'], p['amount'], p['date']) for p in e['promises']]}"
              f" notes={[(n['product_id'], n['reason']) for n in e['notes']]} callbacks={[(x['date'], x['time']) for x in e['callbacks']]}"
              f" transfers={[t['queue'] for t in e['transfers']]}" + (f"  ISSUES: {c['check_issues']}" if c["check_issues"] else ""))


if __name__ == "__main__":
    main()
