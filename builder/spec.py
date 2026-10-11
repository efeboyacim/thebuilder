"""The spec: its JSON Schema (`spec_schema.json`), deterministic validation, paths, patches and the facts the trace
reports.

    report = validate(spec, api, brief_text, rule_ids)
    report.errors      # every error, each naming a spec path: input for the repair step
    report.info        # not_specified paths, residuals by class, rule coverage, neutral choices

Nothing here knows a brief. Code values are checked against the tool API's enums; a free-string code must occur in
the brief text.
"""
import ast, copy, dataclasses, datetime, json, pathlib, re

from builder import bindings as bd

SCHEMA = json.loads(pathlib.Path(__file__).with_name("spec_schema.json").read_text(encoding="utf-8"))
NS = "not_specified"
VERSION = "0"

# Spoken placeholders the engine fills, per template; call-variable names are allowed everywhere.
ITEM_FIELDS = "<item fields>"
PRODUCT_VARS = ("product_name", "due_amount", "days_overdue")
OFFER_VARS = PRODUCT_VARS + ("amount", "date", "deadline", ITEM_FIELDS)
TEMPLATES = {
    "opening.template": (),
    "opening.confirmation.reask_template": (),
    "third_party.ask_reachable_template": (),
    "third_party.deflect_template": (),
    "third_party.callback.ask_time_template": (),
    "third_party.callback.invalid_template": (),
    "identity.questions[].ask_template": (),
    "identity.questions[].reask_template": (),
    "identity.hints[].say_template": (),
    "identity.decline.explain_template": (),
    "disclosure.intro_template": ("product_count",),
    "disclosure.product_template": PRODUCT_VARS + (ITEM_FIELDS,),
    "disclosure.product_names[].name_template": (ITEM_FIELDS,),
    "negotiation.first_ask.template": PRODUCT_VARS + ("amount", "date"),
    "negotiation.first_ask.template_multi": ("product_count", "total_amount", "date"),
    "negotiation.non_business_day.template": OFFER_VARS + ("named_date",),
    "negotiation.after_deadline.template": OFFER_VARS + ("named_date",),
    "negotiation.vague.template": PRODUCT_VARS + ("deadline", ITEM_FIELDS),
    "negotiation.vague.propose_template": OFFER_VARS,
    "negotiation.ask_date_template": PRODUCT_VARS + ("deadline", ITEM_FIELDS),
    "negotiation.ladder[].template": OFFER_VARS,
    "negotiation.amounts.too_much.template": OFFER_VARS,
    "negotiation.amounts.below_floor.template": OFFER_VARS + ("named_amount",),
    "negotiation.amounts.same_amount_template": OFFER_VARS,
    "events[].below_min_template": (),
    "events[].template": (),
    "side_questions.answers[].template": (),
    "side_questions.other.no_info_template": (),
    "confirm.readback_template": ("items", "question"),
    "confirm.item_template": ("product_name", "amount", "date"),
    "confirm.recording_template": (),
    "confirm.recorded_template": ("product_name", "amount", "date"),
    "confirm.after_recorded_sentence": (),
    "confirm.change_after_record_template": (),
    "situations[].say": ("callback_date", "callback_time"),
    "situations[].farewell": ("callback_date", "callback_time"),
}
# Texts anyone may hear before identity is verified (situations reached from these stages are added).
PRE_VERIFICATION = [p for p in TEMPLATES if p.split(".")[0] in ("opening", "third_party", "identity", "side_questions")]
PRE_STAGES = ("open", "third", "identity")
TARGETS = ("on_yes", "on_no", "then", "then_before_verification")
SIDE_RESERVED = ("purpose", "other", "none")

# Expression language: names per expression, helpers everywhere.
HELPERS = ("is_business_day", "add_business_days", "add_calendar_days", "weekday", "roll", "last_bd_of_week",
           "last_bd_of_month", "min", "max", "abs", "ceil_to", "round_half_up")
ITEM_ROLES = ("ref", "kind", "due_amount", "days_overdue")
PRODUCT_NAMES = ITEM_ROLES + (ITEM_FIELDS,)
OFFER_NAMES = ("today", "now", "floor", "deadline", "said_too_much") + PRODUCT_NAMES
EXPRESSIONS = {
    "calendar.named_periods[].date": ("today", "now"),
    "third_party.callback.day_rule": ("d", "today", "now"),
    "products.bands[].selector": ("today",) + PRODUCT_NAMES,
    "products.bands[].deadline": ("today", "now") + PRODUCT_NAMES,
    "products.floor": PRODUCT_NAMES,
    "negotiation.first_ask.amount": ("today",) + PRODUCT_NAMES,
    "negotiation.first_ask.date": ("today", "now") + PRODUCT_NAMES,
    "negotiation.ladder[].amount": OFFER_NAMES,
    "negotiation.ladder[].date": OFFER_NAMES,
}
# What each expression must return on every synthetic input (builder/exprs.py).
EXPR_TYPES = {
    "calendar.named_periods[].date": "date",
    "third_party.callback.day_rule": "bool",
    "products.bands[].selector": "bool",
    "products.bands[].deadline": "date",
    "products.floor": "amount",
    "negotiation.first_ask.amount": "amount",
    "negotiation.first_ask.date": "date",
    "negotiation.ladder[].amount": "amount",
    "negotiation.ladder[].date": "date",
}
_AST_OK = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BinOp, ast.Add,
           ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
           ast.GtE, ast.In, ast.NotIn, ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple)

# Situation references: path -> where it is reached from (pre-verification or not).
SITUATION_REFS = {
    "opening.confirmation.on_unclear": True,
    "third_party.callback.on_booked": True,
    "third_party.callback.on_invalid_exhausted": True,
    "third_party.on_no_answer": True,
    "third_party.terminations[].situation": True,
    "identity.decline.on_declined": True,
    "identity.on_failed": True,
    "closing.hangup.open.situation": True,
    "closing.hangup.third.situation": True,
    "closing.hangup.identity.situation": True,
    "closing.hangup.negotiate.situation": False,
    "closing.hangup.confirm.situation": False,
    "confirm.on_recorded": False,
    "products.bands[].situation": False,
    "products.unmatched": False,
    "negotiation.on_exhausted": False,
    "failure_policy.lookup.on_exhausted": False,
    "failure_policy.record_promise.on_exhausted": False,
    "failure_policy.book_callback.on_exhausted": True,
    "failure_policy.transfer.on_exhausted": False,
}

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_SINGLE = re.compile(r"(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})")


class SpecError(Exception):
    pass


# ---------- JSON Schema (the subset spec_schema.json uses) ----------

def _ref(root, ref):
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def _type_ok(t, v):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def _tname(v):
    return {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", float: "number",
            type(None): "null"}.get(type(v), type(v).__name__)


def _describe(s, root, depth=0):
    if "$ref" in s and depth < 5:
        return _describe(_ref(root, s["$ref"]), root, depth + 1)
    if "const" in s:
        return repr(s["const"])
    if "enum" in s:
        return "one of " + ", ".join(map(repr, s["enum"]))
    if "anyOf" in s:
        return " or ".join(_describe(x, root, depth + 1) for x in s["anyOf"])
    return s.get("type", "any value")


def schema_errors(value, schema=None, path="", root=None):
    root = SCHEMA if root is None else root
    schema = root if schema is None else schema
    where = path or "spec"
    if "$ref" in schema:
        return schema_errors(value, _ref(root, schema["$ref"]), path, root)
    if "anyOf" in schema:
        branches = [schema_errors(value, s, path, root) for s in schema["anyOf"]]
        if any(not b for b in branches):
            return []
        typed = [b for s, b in zip(schema["anyOf"], branches) if not b[0].startswith(f"{where}: expected")
                 and not b[0].startswith(f"{where}: must be") and not b[0].startswith(f"{where}: {value!r} is not")]
        if len(typed) == 1:
            return typed[0]
        return [f"{where}: expected {_describe(schema, root)}, got {_tname(value)} {_short(value)}"]
    if "const" in schema and value != schema["const"]:
        return [f"{where}: must be {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} is not one of {', '.join(map(repr, schema['enum']))}"]
    t = schema.get("type")
    if t and not _type_ok(t, value):
        return [f"{where}: expected {t}, got {_tname(value)} {_short(value)}"]
    errs = []
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{where}: must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        errs.append(f"{where}: {value} is below {schema['minimum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errs.append(f"{_join(path, name)}: missing (required{', may be ' + repr(NS) if _allows_ns(props.get(name), root) else ''})")
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errs += schema_errors(v, props[k], _join(path, k), root)
            elif extra is False:
                errs.append(f"{_join(path, k)}: unknown field (allowed: {', '.join(props)})")
            elif isinstance(extra, dict):
                errs += schema_errors(v, extra, _join(path, k), root)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{where}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += schema_errors(v, schema["items"], f"{path}[{i}]", root)
    return errs


def _allows_ns(s, root):
    return bool(s) and NS in _describe(s, root)


def _short(v):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "..."


def _join(path, key):
    return f"{path}.{key}" if path else key


# ---------- paths and patches ----------

_SEG = re.compile(r"([^.\[\]]+)|\[([^\]]*)\]")


def split_path(path):
    out = []
    for m in _SEG.finditer(path.strip()):
        name, idx = m.group(1), m.group(2)
        if name is not None:
            out.append(name)
        else:
            idx = idx.strip().strip("'\"")
            out.append(int(idx) if re.fullmatch(r"-?\d+", idx) else idx)
    return out


_KEYS = ("id", "name", "kind", "field", "value", "outcome", "rule")


def _child(node, seg):
    if isinstance(node, dict):
        if isinstance(seg, str) and seg in node:
            return True, node[seg]
        return False, None
    if isinstance(node, list):
        if isinstance(seg, int):
            return (True, node[seg]) if -len(node) <= seg < len(node) else (False, None)
        for item in node:
            if isinstance(item, dict) and any(item.get(k) == seg for k in _KEYS):
                return True, item
    return False, None


def resolve(spec, path):
    """(found, value) for a dotted path; a list segment is an index or the id/kind/field of an item."""
    node = spec
    for seg in split_path(path):
        ok, node = _child(node, seg)
        if not ok:
            return False, None
    return True, node


def apply_patches(spec, patches):
    """A copy of spec with patches applied: [{"op": set|remove|append, "path": ..., "value": ...}]. Returns
    (spec, errors); a patch that cannot apply is reported and skipped."""
    spec, errs = copy.deepcopy(spec), []
    for n, p in enumerate(patches or []):
        try:
            op, segs = p.get("op", "set"), split_path(p["path"])
            if not segs:
                raise SpecError("empty path")
            parent = spec
            for i, seg in enumerate(segs[:-1]):
                ok, child = _child(parent, seg)
                if not ok:
                    if op == "remove" or not isinstance(parent, dict) or not isinstance(seg, str):
                        raise SpecError(f"no {'.'.join(map(str, segs[:i + 1]))}")
                    child = parent[seg] = [] if isinstance(segs[i + 1], int) else {}
                parent = child
            last = segs[-1]
            if isinstance(parent, list) and isinstance(last, str):
                last = next((j for j, it in enumerate(parent) if isinstance(it, dict)
                             and any(it.get(k) == last for k in _KEYS)), None)
                if last is None:
                    if op == "set":
                        parent.append(p["value"])
                        continue
                    raise SpecError(f"no item {segs[-1]!r}")
            if op == "set":
                if isinstance(parent, list) and last == len(parent):
                    parent.append(p["value"])
                else:
                    parent[last] = p["value"]
            elif op == "remove":
                del parent[last]
            elif op == "append":
                target = parent[last] if not (isinstance(parent, dict) and last not in parent) else parent.setdefault(last, [])
                if not isinstance(target, list):
                    raise SpecError("append needs a list")
                target.append(p["value"])
            else:
                raise SpecError(f"unknown op {op!r}")
        except (SpecError, KeyError, IndexError, TypeError) as e:
            errs.append(f"patch {n} ({p.get('op')} {p.get('path')}): {e}")
    return spec, errs


def walk(node, path=""):
    """(path, value) for every leaf."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, _join(path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, node


def expand(spec, pattern):
    """(path, value) for a pattern such as 'identity.questions[].ask_template'; missing parts yield nothing."""
    out = [("", spec)]
    for seg in pattern.split("."):
        name, star = (seg[:-2], True) if seg.endswith("[]") else (seg, False)
        nxt = []
        for p, node in out:
            if isinstance(node, dict) and name in node:
                v, vp = node[name], _join(p, name)
                if star:
                    if isinstance(v, list):
                        nxt += [(f"{vp}[{i}]", x) for i, x in enumerate(v)]
                else:
                    nxt.append((vp, v))
        out = nxt
    return out


# ---------- expressions ----------

def expr_errors(text, names, helpers=HELPERS):
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as e:
        return [f"not a valid expression ({e.msg})"]
    errs = []
    for node in ast.walk(tree):
        if not isinstance(node, _AST_OK):
            errs.append(f"{type(node).__name__} is not allowed")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in helpers:
                errs.append(f"only these functions may be called: {', '.join(helpers)}")
            if node.keywords:
                errs.append("keyword arguments are not allowed")
        elif isinstance(node, ast.Name) and node.id not in names and node.id not in helpers:
            errs.append(f"unknown name {node.id!r} (known: {', '.join(sorted(names))})")
        elif isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str, bool)):
            errs.append(f"constant {node.value!r} is not allowed")
    return sorted(set(errs))


# ---------- validation ----------

@dataclasses.dataclass
class Report:
    errors: list
    warnings: list
    info: dict

    @property
    def ok(self):
        return not self.errors


def _norm(s):
    s = _PLACEHOLDER.sub(lambda m: "{" + m.group(1) + "}", str(s))
    s = re.sub(r"[*`|_>#]", " ", s)
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().casefold()


def occurs(needle, haystack):
    """needle occurs in haystack as a whole phrase, ignoring case, spacing, Markdown and {x}/{{x}}."""
    n = _norm(needle).strip(" .?!")
    return bool(n) and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", _norm(haystack)) is not None


class _Ctx:
    def __init__(self, spec, api):
        self.spec, self.api = spec, api
        b = spec.get("bindings") if isinstance(spec.get("bindings"), dict) else {}
        self.b = bd.Bindings.from_dict(b) if b else bd.Bindings({}, {}, {}, {})
        self.call_vars = [v.get("name") for v in spec.get("call_variables") or [] if isinstance(v, dict)]

    def tool(self, role):
        name = self.b.tools.get(role)
        return self.api.tools.get(name) if isinstance(name, str) else None

    def request_field(self, role, engine_role):
        t = self.tool(role)
        hit = self.b.engine_fields(role).get(engine_role) if t else None
        return t.request.get(hit[0]) if hit else None

    def request_enum(self, role, engine_role):
        f = self.request_field(role, engine_role)
        return list(f.enum) if f is not None and f.enum else None

    def response_enum(self, role, resp_role, item=False):
        t, resp = self.tool(role), self.b.response.get(role) or {}
        if not t:
            return None
        path = resp.get(resp_role) if not item else (
            f"{resp.get('products')}[].{(resp.get('item') or {}).get(resp_role)}" if resp.get("products") else None)
        f = t.response.get(path) if isinstance(path, str) else None
        return list(f.enum) if f is not None and f.enum else None

    def item_fields(self):
        t, resp = self.tool("lookup"), self.b.response.get("lookup") or {}
        lst = resp.get("products")
        if not t or not isinstance(lst, str):
            return []
        pre = lst + "[]."
        return [p[len(pre):] for p in t.response if p.startswith(pre) and "." not in p[len(pre):]]


def validate(spec, api, brief_text="", rule_ids=None, known_ids=None):
    """Every error of a spec against its schema, the tool API, the brief text and the digest. rule_ids: the rules that
    must be indexed (binding ones); known_ids: every digest rule id (default rule_ids)."""
    errors = schema_errors(spec) if isinstance(spec, dict) else ["spec: expected an object"]
    warnings, info = [], {}
    if not isinstance(spec, dict):
        return Report(errors, warnings, info)
    ctx = _Ctx(spec, api)
    for check in (_check_bindings, _check_codes, _check_situations, _check_templates, _check_expressions,
                  _check_identity, _check_calendar, _check_callback, _check_negotiation, _check_events,
                  _check_pre_verification, _check_honesty, _check_phrases):
        try:
            errors += check(ctx, brief_text)
        except Exception as e:  # noqa: BLE001 - a malformed part is already a schema error; keep validating
            warnings.append(f"check {check.__name__[7:]} could not run: {type(e).__name__}: {e}")
    try:
        errs, cov = _check_index(ctx, rule_ids, known_ids)
        errors += errs
        info["rule_coverage"] = cov
    except Exception as e:  # noqa: BLE001
        warnings.append(f"check index could not run: {type(e).__name__}: {e}")
    info.update(facts(spec))
    return Report(errors, warnings, info)


def facts(spec):
    """What the trace reports about a spec: not_specified paths, residuals, neutral choices."""
    ns = [p for p, v in walk(spec) if v == NS]
    res = [r for r in spec.get("residual_rules") or [] if isinstance(r, dict)]
    by_class = {}
    for r in res:
        by_class[r.get("class")] = by_class.get(r.get("class"), 0) + 1
    hang = (spec.get("closing") or {}).get("hangup") or {}
    neutral = sorted(k for k, v in hang.items() if isinstance(v, dict) and v.get("stated_in_brief") is False)
    return {"not_specified": ns, "residuals": by_class,
            "flow_residuals": [{"id": r.get("id"), "stage": r.get("stage"), "trigger": r.get("trigger")}
                               for r in res if r.get("class") == "flow"],
            "hangup_chosen_by_model": neutral,
            "situations": len(spec.get("situations") or []),
            "bands": [b.get("id") for b in (spec.get("products") or {}).get("bands") or [] if isinstance(b, dict)],
            "ladder_steps": len((spec.get("negotiation") or {}).get("ladder") or []),
            "events": [e.get("id") for e in spec.get("events") or [] if isinstance(e, dict)],
            "side_questions": [a.get("id") for a in (spec.get("side_questions") or {}).get("answers") or []
                               if isinstance(a, dict)],
            "identity_questions": [q.get("field") for q in (spec.get("identity") or {}).get("questions") or []
                                   if isinstance(q, dict)]}


def _check_bindings(ctx, _):
    b = ctx.spec.get("bindings")
    if not isinstance(b, dict):
        return []
    errs = ["bindings." + e for e in bd.validate(ctx.b, ctx.api, ctx.call_vars)]
    for role in ("customer_key", "clock"):
        if role not in ctx.b.call_vars:
            errs.append(f"bindings.call_vars.{role}: required role has no call variable")
    needed = {"verify", "lookup", "record_promise", "close"}
    cb = ((ctx.spec.get("third_party") or {}).get("callback") or {})
    if cb.get("enabled") is True:
        needed.add("book_callback")
    if any(isinstance(s, dict) and s.get("queue") for s in ctx.spec.get("situations") or []):
        needed.add("transfer")
    for role in sorted(needed - set(ctx.b.tools)):
        errs.append(f"bindings.tools.{role}: the spec needs this tool role; bind it to a tool")
    return errs


def _code_check(where, value, allowed, brief_text, forbidden):
    if value in forbidden:
        return [f"{where}: {value!r} is a forbidden code"]
    if allowed is not None:
        return [] if value in allowed else [f"{where}: {value!r} is not one of the tool's values {allowed}"]
    if brief_text and not re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", brief_text):
        return [f"{where}: {value!r} does not occur in the brief (a free-text code must be copied verbatim)"]
    return []


def _check_codes(ctx, brief_text):
    s, errs = ctx.spec, []
    forbidden = set(c for c in s.get("forbidden_codes") or [] if isinstance(c, str))
    for i, c in enumerate(s.get("forbidden_codes") or []):
        if c in ctx.api.tools:
            errs.append(f"forbidden_codes[{i}]: {c!r} is a tool name, not a code; leave the tool out of bindings.tools")
    outcomes = ctx.request_enum("close", "outcome")
    reasons = ctx.request_enum("close", "reason")
    queues = ctx.request_enum("transfer", "queue")
    notes = ctx.request_enum("record_note", "note_reason")
    for i, sit in enumerate(s.get("situations") or []):
        if not isinstance(sit, dict):
            continue
        w = f"situations[{i}]"
        if isinstance(sit.get("outcome"), str):
            errs += _code_check(f"{w}.outcome", sit["outcome"], outcomes, brief_text, forbidden)
        for key, allowed in (("reason", reasons), ("queue", queues), ("note_reason", notes)):
            if isinstance(sit.get(key), str) and sit[key]:
                errs += _code_check(f"{w}.{key}", sit[key], allowed, brief_text, forbidden)
    for i, row in enumerate((s.get("closing") or {}).get("outcome_table") or []):
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("outcome"), str):
            errs += _code_check(f"closing.outcome_table[{i}].outcome", row["outcome"], outcomes, brief_text, forbidden)
        for j, r in enumerate(row.get("reasons") or []):
            if isinstance(r, str):
                errs += _code_check(f"closing.outcome_table[{i}].reasons[{j}]", r, reasons, brief_text, forbidden)
    fields = ctx.request_enum("verify", "question")
    for i, q in enumerate((s.get("identity") or {}).get("questions") or []):
        if isinstance(q, dict) and isinstance(q.get("field"), str):
            errs += _code_check(f"identity.questions[{i}].field", q["field"], fields, brief_text, forbidden)
    hints = ctx.response_enum("verify", "hint")
    for i, h in enumerate((s.get("identity") or {}).get("hints") or []):
        if isinstance(h, dict) and isinstance(h.get("value"), str):
            errs += _code_check(f"identity.hints[{i}].value", h["value"], hints, brief_text, forbidden)
    kinds = ctx.response_enum("lookup", "kind", item=True)
    named = []
    for i, p in enumerate((s.get("disclosure") or {}).get("product_names") or []):
        if isinstance(p, dict) and isinstance(p.get("kind"), str):
            named.append(p["kind"])
            errs += _code_check(f"disclosure.product_names[{i}].kind", p["kind"], kinds, brief_text, forbidden)
    for k in kinds or []:
        if k not in named:
            errs.append(f"disclosure.product_names: product kind {k!r} has no name")
    return errs


def _check_situations(ctx, _):
    s, errs = ctx.spec, []
    sits = [x for x in s.get("situations") or [] if isinstance(x, dict)]
    ids = [x.get("id") for x in sits]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(f"situations: id {d!r} is used twice")
    table = {}
    for row in (s.get("closing") or {}).get("outcome_table") or []:
        if isinstance(row, dict) and isinstance(row.get("outcome"), str):
            table[row["outcome"]] = [r for r in row.get("reasons") or [] if isinstance(r, str)]
    for i, x in enumerate(sits):
        o, r = x.get("outcome"), x.get("reason")
        if table and o not in table:
            errs.append(f"situations[{i}].outcome: {o!r} is not in closing.outcome_table")
        elif table and isinstance(r, str):
            if not table[o] and r:
                errs.append(f"situations[{i}].reason: outcome {o!r} takes no reason, so reason must be empty")
            elif table[o] and r not in table[o]:
                errs.append(f"situations[{i}].reason: {r!r} is not a reason of {o!r} ({table[o]})")
    have = {(x.get("outcome"), x.get("reason") or "") for x in sits}
    for o, rs in table.items():
        for r in rs or [""]:
            if (o, r) not in have:
                errs.append(f"situations: no situation for outcome {o!r}{' reason ' + repr(r) if r else ''} of the outcome table")
    for pattern in SITUATION_REFS:
        for path, v in expand(s, pattern):
            if isinstance(v, str) and v != NS and v not in ids:
                errs.append(f"{path}: {v!r} is not a situation id")
    return errs


def _check_templates(ctx, _):
    s, errs = ctx.spec, []
    item_fields = ctx.item_fields()
    for pattern, extra in TEMPLATES.items():
        allowed = set(ctx.call_vars) | {x for x in extra if x != ITEM_FIELDS}
        if ITEM_FIELDS in extra:
            allowed |= set(item_fields)
        for path, text in expand(s, pattern):
            if not isinstance(text, str) or text == NS:
                continue
            for name in _PLACEHOLDER.findall(text):
                if name not in allowed:
                    errs.append(f"{path}: placeholder {{{{{name}}}}} is not allowed here (allowed: {', '.join(sorted(allowed)) or 'none'})")
            for name in _SINGLE.findall(text):
                errs.append(f"{path}: write the placeholder {{{name}}} as {{{{{name}}}}}")
    c = s.get("confirm") or {}
    if isinstance(c.get("readback_template"), str):
        for need in ("items", "question"):
            if need not in _PLACEHOLDER.findall(c["readback_template"]):
                errs.append(f"confirm.readback_template: must contain {{{{{need}}}}}")
    return errs


def _check_expressions(ctx, _):
    """Names and the AST whitelist first; an expression that passes is then evaluated on synthetic inputs."""
    from builder import exprs
    errs, bad, item_fields = [], set(), ctx.item_fields()
    for pattern, names in EXPRESSIONS.items():
        allowed = {n for n in names if n != ITEM_FIELDS} | (set(item_fields) if ITEM_FIELDS in names else set())
        for path, text in expand(ctx.spec, pattern):
            if isinstance(text, str) and text != NS:
                found = [f"{path}: {e}" for e in expr_errors(text, allowed)]
                errs += found
                if found:
                    bad.add(path)
    return errs + exprs.check_values(ctx.spec, ctx.api, skip=bad)


def _check_identity(ctx, _):
    ident, errs = ctx.spec.get("identity") or {}, []
    qs = [q for q in ident.get("questions") or [] if isinstance(q, dict)]
    fields = [q.get("field") for q in qs]
    for d in sorted({f for f in fields if fields.count(f) > 1 and isinstance(f, str)}):
        errs.append(f"identity.questions: field {d!r} is asked twice")
    for i, q in enumerate(qs):
        pat = q.get("complete_pattern")
        if isinstance(pat, str):
            try:
                re.compile(pat)
            except re.error as e:
                errs.append(f"identity.questions[{i}].complete_pattern: not a valid regular expression ({e})")
        if q.get("answer_type") == "digits" and q.get("digits_count") == NS:
            pass  # allowed: the pattern still decides completeness
    for i, h in enumerate(ident.get("hints") or []):
        if isinstance(h, dict) and isinstance(h.get("question"), str) and h["question"] not in fields + ["any"]:
            errs.append(f"identity.hints[{i}].question: {h['question']!r} is neither a question field nor 'any'")
    return errs


def _check_calendar(ctx, _):
    errs = []
    for i, d in enumerate((ctx.spec.get("calendar") or {}).get("holidays") or []):
        try:
            datetime.date.fromisoformat(d)
        except (TypeError, ValueError):
            errs.append(f"calendar.holidays[{i}]: {d!r} is not a date")
    return errs


def _check_negotiation(ctx, _):
    s, errs = ctx.spec, []
    bands = [b for b in (s.get("products") or {}).get("bands") or [] if isinstance(b, dict)]
    ids = [b.get("id") for b in bands]
    for d in sorted({i for i in ids if ids.count(i) > 1 and isinstance(i, str)}):
        errs.append(f"products.bands: id {d!r} is used twice")
    for i, b in enumerate(bands):
        if b.get("action") == "close" and b.get("situation") == NS:
            errs.append(f"products.bands[{i}].situation: a band with action close needs a situation")
    neg = s.get("negotiation") or {}
    steps = [x for x in neg.get("ladder") or [] if isinstance(x, dict)]
    nums = [x.get("step") for x in steps]
    if nums != sorted(nums) or len(set(nums)) != len(nums):
        errs.append("negotiation.ladder: steps must be numbered in ascending order, each once")
    for i, x in enumerate(steps):
        for j, b in enumerate(x.get("bands") or []):
            if b not in ids:
                errs.append(f"negotiation.ladder[{i}].bands[{j}]: {b!r} is not a band id of products.bands")
    for key, policy, needs in (("non_business_day", ("next", "prev"), "template"),
                               ("after_deadline", ("offer_deadline",), "template"),
                               ("vague", ("propose_deadline",), "propose_template")):
        part = neg.get(key) or {}
        field = "then" if key == "vague" else "policy"
        if part.get(field) in policy and part.get(needs) in (None, NS):
            errs.append(f"negotiation.{key}.{needs}: {field} {part.get(field)!r} needs this template")
    phrases = [p.get("phrase") for p in (s.get("calendar") or {}).get("named_periods") or [] if isinstance(p, dict)]
    for d in sorted({p for p in phrases if phrases.count(p) > 1 and isinstance(p, str)}):
        errs.append(f"calendar.named_periods: phrase {d!r} is listed twice")
    return errs


def _check_events(ctx, _):
    s, errs = ctx.spec, []
    sit_ids = {x.get("id") for x in s.get("situations") or [] if isinstance(x, dict)}
    evs = [e for e in s.get("events") or [] if isinstance(e, dict)]
    ids = [e.get("id") for e in evs]
    side = [a.get("id") for a in (s.get("side_questions") or {}).get("answers") or [] if isinstance(a, dict)]
    for d in sorted({i for i in ids if ids.count(i) > 1 and isinstance(i, str)}):
        errs.append(f"events: id {d!r} is used twice")
    for d in sorted({i for i in side if side.count(i) > 1 and isinstance(i, str)}):
        errs.append(f"side_questions.answers: id {d!r} is used twice")
    for i, a in enumerate(side):
        if a in SIDE_RESERVED:
            errs.append(f"side_questions.answers[{i}].id: {a!r} is reserved; choose another id")
    for i, e in enumerate(evs):
        w = f"events[{i}]"
        if e.get("id") == "none":
            errs.append(f"{w}.id: 'none' is reserved")
        for j, x in enumerate(e.get("suppresses") or []):
            if x != "persuasion" and x not in ids:
                errs.append(f"{w}.suppresses[{j}]: {x!r} is neither an event id nor 'persuasion'")
        for k in TARGETS:
            v = e.get(k)
            if isinstance(v, str) and v not in ("continue", NS) and v not in sit_ids:
                errs.append(f"{w}.{k}: {v!r} is not a situation id, 'continue' or 'not_specified'")
        asks = any(e.get(k) not in (None, NS) for k in ("on_yes", "on_no"))
        if asks and e.get("template") in (None, NS):
            errs.append(f"{w}.template: on_yes/on_no need a template that asks the question")
        if not asks and e.get("then") in (None, NS) and e.get("below_min_template") in (None, NS):
            errs.append(f"{w}: the event does nothing; set then, or a question with on_yes and on_no")
    return errs


def _check_callback(ctx, _):
    cb, errs = ((ctx.spec.get("third_party") or {}).get("callback") or {}), []
    wins = cb.get("windows")
    if isinstance(wins, list):
        for i, w in enumerate(wins):
            if isinstance(w, dict) and isinstance(w.get("start"), str) and isinstance(w.get("end"), str) \
                    and w["start"] >= w["end"]:
                errs.append(f"third_party.callback.windows[{i}]: start must be before end")
    if cb.get("enabled") is True:
        for k in ("on_booked",):
            if cb.get(k) == NS:
                errs.append(f"third_party.callback.{k}: callbacks are enabled, so this situation is needed")
    return errs


def _pre_verification_texts(ctx):
    s = ctx.spec
    out = [(p, t) for pat in PRE_VERIFICATION for p, t in expand(s, pat) if isinstance(t, str)]
    sits = {x.get("id"): (i, x) for i, x in enumerate(s.get("situations") or []) if isinstance(x, dict)}
    for pattern, pre in SITUATION_REFS.items():
        if not pre:
            continue
        for _, sid in expand(s, pattern):
            if sid in sits:
                i, x = sits[sid]
                out += [(f"situations[{i}].{k}", x.get(k)) for k in ("say", "farewell") if isinstance(x.get(k), str)]
    for n, e in enumerate(s.get("events") or []):
        if not isinstance(e, dict) or not set(e.get("stages") or []) & set(PRE_STAGES):
            continue
        out += [(f"events[{n}].{k}", e.get(k)) for k in ("template", "below_min_template") if isinstance(e.get(k), str)]
        keys = ["then_before_verification"] if e.get("then_before_verification") not in (None, NS) else ["then", "on_yes"]
        for sid in [e.get(k) for k in keys + ["on_no"]]:
            if sid in sits:
                i, x = sits[sid]
                out += [(f"situations[{i}].{k}", x.get(k)) for k in ("say", "farewell") if isinstance(x.get(k), str)]
    return out


def _check_pre_verification(ctx, _):
    words = [w for w in (ctx.spec.get("opening") or {}).get("forbidden_before_verification") or []
             if isinstance(w, str) and w.strip()]
    errs, seen = [], set()
    for path, text in _pre_verification_texts(ctx):
        plain = _PLACEHOLDER.sub(" ", text)
        for w in words:
            if (path, w) not in seen and occurs(w, plain):
                seen.add((path, w))
                errs.append(f"{path}: says {w!r}, which is forbidden before identity is verified")
    return errs


AFTER_WRITE = ("confirm.recorded_template", "confirm.after_recorded_sentence", "confirm.change_after_record_template",
               "situations[].say", "situations[].farewell")


def _check_honesty(ctx, _):
    """The word that claims a successful write (a participle of confirm.recorded_template) is said nowhere before."""
    rec = (ctx.spec.get("confirm") or {}).get("recorded_template")
    if not isinstance(rec, str):
        return []
    words = {w for w in re.findall(r"[a-z]{5,}ed", _PLACEHOLDER.sub(" ", rec).lower())}
    errs = []
    for pattern in TEMPLATES:
        if pattern in AFTER_WRITE:
            continue
        for path, text in expand(ctx.spec, pattern):
            if not isinstance(text, str) or text == NS:
                continue
            for w in sorted(words):
                if re.search(r"\b" + w + r"\b", text.lower()):
                    errs.append(f"{path}: says {w!r} before any write succeeded; a literal reader takes it as a claim "
                                f"that something was {w} (say it with another word)")
    return errs


def _check_phrases(ctx, brief_text):
    errs = []
    for i, ph in enumerate(ctx.spec.get("phrases") or []):
        if not isinstance(ph, dict) or not isinstance(ph.get("text"), str):
            continue
        if brief_text and not occurs(ph["text"], brief_text):
            errs.append(f"phrases[{i}].text: {ph['text']!r} is not verbatim in the brief")
        for path in ph.get("used_in") or []:
            if not isinstance(path, str):
                continue
            if path.startswith("residual:"):
                if not any(isinstance(r, dict) and r.get("id") == path[9:] for r in ctx.spec.get("residual_rules") or []):
                    errs.append(f"phrases[{i}].used_in: no residual rule {path[9:]!r}")
                continue
            found, v = resolve(ctx.spec, path)
            if not found:
                errs.append(f"phrases[{i}].used_in: no spec path {path!r}")
            elif isinstance(v, str) and not occurs(ph["text"], v):
                errs.append(f"phrases[{i}]: {path} does not contain {ph['text']!r} word for word")
    return errs


def _check_index(ctx, rule_ids, known_ids=None):
    s, errs = ctx.spec, []
    res_ids = [r.get("id") for r in s.get("residual_rules") or [] if isinstance(r, dict)]
    for d in sorted({i for i in res_ids if res_ids.count(i) > 1 and i}):
        errs.append(f"residual_rules: id {d!r} is used twice")
    for i, r in enumerate(s.get("residual_rules") or []):
        if isinstance(r, dict) and r.get("class") == "flow" and not str(r.get("trigger") or "").strip():
            errs.append(f"residual_rules[{i}].trigger: a flow rule needs a trigger")
    indexed = []
    for i, row in enumerate(s.get("rule_index") or []):
        if not isinstance(row, dict):
            continue
        indexed.append(row.get("rule"))
        for path in row.get("paths") or []:
            if not isinstance(path, str):
                continue
            if path.startswith("residual:"):
                if path[9:] not in res_ids:
                    errs.append(f"rule_index[{i}].paths: no residual rule {path[9:]!r}")
            elif not resolve(s, path)[0]:
                errs.append(f"rule_index[{i}].paths: no spec path {path!r}")
    for d in sorted({r for r in indexed if indexed.count(r) > 1 and r}):
        errs.append(f"rule_index: rule {d} is indexed twice; merge its paths into one entry")
    cov = None
    if rule_ids is not None:
        known = set(known_ids if known_ids is not None else rule_ids)
        missing = [r for r in rule_ids if r not in indexed]
        for r in missing:
            errs.append(f"rule_index: digest rule {r} is not indexed (add its spec paths or a residual rule)")
        for r in sorted({r for r in indexed if r not in known and r}, key=_rule_key):
            errs.append(f"rule_index: {r} is not a digest rule")
        bad_refs = sorted({r for _, r in _refs(s) if r not in known}, key=_rule_key)
        if bad_refs:
            errs.append(f"refs: unknown digest rule id(s) {', '.join(bad_refs)}")
        cov = {"rules": len(rule_ids), "indexed": len(rule_ids) - len(missing)}
    return errs, cov


def _refs(spec):
    for path, v in walk(spec):
        if re.search(r"(^|\.)refs\[\d+\]$", path):
            yield path, v


def _rule_key(r):
    m = re.search(r"\d+", str(r))
    return (int(m.group()) if m else 0, str(r))
