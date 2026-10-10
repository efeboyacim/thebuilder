"""An offline bank built from the tool API file and a binding set, for local replay and conformance tests.

It validates every request body against the tool's request schema (HTTP 400 when it does not conform), plays one
customer given in engine roles, and answers with schema-shaped bodies built through the response bindings. Every
answer it builds is checked against the response schema, so a binding that cannot produce a valid response fails
loudly. Paths: correct, wrong and hinted identity answers, an unreadable answer (ok false, not counted), a lock after
the wrong-answer limit, lookups refused before verification, and per-tool faults: HTTP 500 (ok false body, nothing
recorded), ok false, and a timeout (408, no body). The ledger records what a bank would have recorded.

Customer, in engine roles (test data or LLM-written scenarios; nothing here has a default from a brief):

    {"identity": {"answers": {question: answer}, "limit": n or None,
                  "hints": [{"question": q, "answer": given, "hint": value}],
                  "readable": {question: regex}},
     "products": [{"ref": "p1", "kind": ..., "due_amount": ..., "days_overdue": ..., "fields": {api field: value}}]}
"""
import dataclasses, re

from builder import bindings as bd
from builder import toolapi as ta

FAULTS = ("500", "ok_false", "timeout")
GATED = ("lookup", "record_promise", "record_note")  # need every identity answer passed


class StubError(Exception):
    """The stub cannot answer in the shape the tool API promises: a binding or fixture bug."""


@dataclasses.dataclass
class StubResponse:
    status: int
    body: object

    @property
    def ok(self):
        return self.status == 200


def _norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


class StubBank:
    def __init__(self, api, bindings, customer, faults=None, close_rules=None, gated=GATED):
        self.api, self.b, self.customer = api, bindings, customer
        self.faults = {role: list(kinds) for role, kinds in (faults or {}).items()}
        for kinds in self.faults.values():
            bad = [k for k in kinds if k not in FAULTS + ("none",)]
            if bad:
                raise StubError(f"unknown fault {bad} (one of {', '.join(FAULTS)}, none)")
        self.close_rules = close_rules  # {outcome: [reasons]}; [] = no reason allowed; None = accept enum-valid
        self.gated = gated
        ident = customer.get("identity") or {}
        self.answers = dict(ident.get("answers") or {})
        self.limit = ident.get("limit")
        self.hints = list(ident.get("hints") or [])
        self.readable = dict(ident.get("readable") or {})
        self.passed, self.closed = set(), False
        self.ledger = {"calls": [], "verified": False, "wrong": 0, "hinted": 0, "unreadable": 0, "promises": [],
                       "notes": [], "callbacks": [], "transfers": [], "outcome": None, "reason": None,
                       "violations": []}

    # ----- entry point -----

    def call(self, tool_name, body):
        role = self.b.role_of(tool_name)
        entry = {"tool": tool_name, "role": role, "body": body}
        self.ledger["calls"].append(entry)
        resp = self._call(tool_name, role, body, entry)
        entry.update(status=resp.status, response=resp.body)
        return resp

    def _call(self, tool_name, role, body, entry):
        tool = self.api.tools.get(tool_name)
        if tool is None:
            return StubResponse(404, None)
        errors = ta.validate_body(tool, body)
        if errors:
            entry["rejected"] = errors
            self.ledger["violations"].append(f"{tool_name}: invalid request: {'; '.join(errors)}")
            return StubResponse(400, self._shaped(role, tool, {"ok": False, "error": "invalid request"}, check=False))
        if role is None:
            return StubResponse(200, self._shaped(role, tool, {"ok": True}))
        if self.closed:
            self.ledger["violations"].append(f"{tool_name}: called after the call was closed")
            return self._fail(role, tool, "the call is closed")
        if role in self.gated and not self._all_passed():
            self.ledger["violations"].append(f"{tool_name}: called before identity was verified")
            return self._fail(role, tool, "identity not verified")
        fault = self.faults.get(role, []).pop(0) if self.faults.get(role) else "none"
        entry["fault"] = fault
        if fault == "500":
            return StubResponse(500, self._shaped(role, tool, {"ok": False, "error": "service unavailable, nothing was recorded"}, check=False))
        if fault == "timeout":
            return StubResponse(408, None)
        if fault == "ok_false":
            return self._fail(role, tool, "not carried out")
        values = bd.parse_body(self.b, role, body)
        return getattr(self, "_" + role)(tool, values)

    # ----- helpers -----

    def _all_passed(self):
        return bool(self.answers) and set(self.answers) <= self.passed

    def _shaped(self, role, tool, values, items=None, check=True):
        body = bd.build_response(self.b, role, values, items) if role else {}
        for path, f in tool.response.items():  # required fields no role covers get a placeholder
            if f.required and "." not in path and "[]" not in path and path not in body:
                body[path] = ta.example(tool.response_schema["properties"][path])
        if check and tool.response_schema:
            errs = ta.check(tool.response_schema, body, "response")
            if errs:
                raise StubError(f"{tool.name}: response does not fit the schema: {'; '.join(errs)}")
        return body

    def _fail(self, role, tool, error):
        if "ok" not in (self.b.response.get(role) or {}):
            return StubResponse(409, self._shaped(role, tool, {"error": error}, check=False))
        return StubResponse(200, self._shaped(role, tool, {"ok": False, "error": error}))

    def _ok(self, role, tool, values=None, items=None):
        return StubResponse(200, self._shaped(role, tool, {"ok": True, **(values or {})}, items))

    # ----- tool roles -----

    def _verify(self, tool, v):
        if self.limit is not None and self.ledger["wrong"] >= self.limit:
            return self._fail("verify", tool, "too many wrong answers")
        q = v.get("question")
        if q is None and len(self.answers) == 1:
            q = next(iter(self.answers))
        if q not in self.answers:
            return self._fail("verify", tool, f"unknown question {q!r}")
        given = v.get("answer")
        if q in self.readable and not re.search(self.readable[q], str(given)):
            self.ledger["unreadable"] += 1
            return self._fail("verify", tool, "unreadable answer")
        left = lambda: None if self.limit is None else self.limit - self.ledger["wrong"]
        for h in self.hints:
            if h.get("question") == q and _norm(h.get("answer")) == _norm(given):
                self.ledger["hinted"] += 1
                return self._ok("verify", tool, {"verified": False, "all_verified": self._all_passed(),
                                                 "attempts_left": left(), "hint": h["hint"]})
        if _norm(given) == _norm(self.answers[q]):
            self.passed.add(q)
            self.ledger["verified"] = self._all_passed()
            return self._ok("verify", tool, {"verified": True, "all_verified": self._all_passed(),
                                             "attempts_left": left()})
        self.ledger["wrong"] += 1
        return self._ok("verify", tool, {"verified": False, "all_verified": self._all_passed(),
                                         "attempts_left": left()})

    def _lookup(self, tool, v):
        return self._ok("lookup", tool, items=self.customer.get("products") or [])

    def _product(self, ref):
        return next((p for p in self.customer.get("products") or [] if str(p.get("ref")) == str(ref)), None)

    def _record_promise(self, tool, v):
        ref = v.get("product_ref")
        if self._product(ref) is None:
            self.ledger["violations"].append(f"promise for unknown product {ref!r}")
            return self._fail("record_promise", tool, "unknown product")
        if any(p[0] == ref for p in self.ledger["promises"]):
            self.ledger["violations"].append(f"second promise for product {ref!r}")
            return self._fail("record_promise", tool, "a promise for this product already exists")
        self.ledger["promises"].append((ref, round(float(v["amount"]), 2), v["date"]))
        n = len(self.ledger["promises"])
        f = tool.response.get((self.b.response.get("record_promise") or {}).get("promise_ref", ""))
        return self._ok("record_promise", tool, {"promise_ref": n if f and f.type == "integer" else f"pr_{n}"})

    def _record_note(self, tool, v):
        ref = v.get("product_ref")
        if self._product(ref) is None:
            self.ledger["violations"].append(f"note for unknown product {ref!r}")
            return self._fail("record_note", tool, "unknown product")
        self.ledger["notes"].append((ref, v.get("note_reason")))
        return self._ok("record_note", tool)

    def _book_callback(self, tool, v):
        self.ledger["callbacks"].append((v.get("date"), v.get("time")))
        return self._ok("book_callback", tool)

    def _transfer(self, tool, v):
        self.ledger["transfers"].append(v.get("queue"))
        return self._ok("transfer", tool)

    def _close(self, tool, v):
        outcome, reason = v.get("outcome"), v.get("reason")
        if self.close_rules is not None:
            allowed = self.close_rules.get(outcome)
            if allowed is None or (reason not in allowed if allowed else reason not in (None, "")):
                return self._fail("close", tool, f"({outcome}, {reason}) is not in the outcome table")
        self.ledger["outcome"], self.ledger["reason"], self.closed = outcome, reason, True
        return self._ok("close", tool)
