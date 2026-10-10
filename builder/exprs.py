"""Spec expressions: translation to Python source for the brain, and evaluation on synthetic inputs at build time.

    to_source("d == today or d == add_business_days(today, 1)")
        -> '((n["d"] == n["today"]) or (n["d"] == h["add_business_days"](n["today"], 1)))'
    compile_all(spec)            # {path: callable(n, h)}, built from the same source the brain embeds
    check_values(spec, api)      # errors: an expression that raises or returns the wrong type on a synthetic input

The source is emitted by a small printer for the whitelisted subset (builder.spec.expr_errors), so the brain needs no
`eval` and no `ast`. Synthetic inputs cover Fridays, weekends, the days around every holiday, after-hours and
early-morning clocks, and products of every kind across a range of amounts and days overdue.
"""
import ast, datetime

from builder import policy_lib as pl
from builder import spec as sp

_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%"}
_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.In: "in",
        ast.NotIn: "not in"}


class ExprError(Exception):
    pass


def to_source(text):
    """Python source of an expression over n (names) and h (helpers)."""
    errs = sp.expr_errors(text, names=_Any())
    if errs:
        raise ExprError("; ".join(errs))
    return _emit(ast.parse(text.strip(), mode="eval").body)


class _Any:
    """Accepts every name: the name check is the validator's job; here only the node whitelist matters."""

    def __contains__(self, _):
        return True

    def __iter__(self):
        return iter(())


def _emit(node):
    if isinstance(node, ast.BoolOp):
        op = " and " if isinstance(node.op, ast.And) else " or "
        return "(" + op.join(_emit(v) for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp):
        op = {ast.Not: "not ", ast.USub: "-", ast.UAdd: "+"}[type(node.op)]
        return "(" + op + _emit(node.operand) + ")"
    if isinstance(node, ast.BinOp):
        return "(" + _emit(node.left) + " " + _BIN[type(node.op)] + " " + _emit(node.right) + ")"
    if isinstance(node, ast.Compare):
        parts = [_emit(node.left)]
        for op, right in zip(node.ops, node.comparators):
            parts += [_CMP[type(op)], _emit(right)]
        return "(" + " ".join(parts) + ")"
    if isinstance(node, ast.IfExp):
        return "(" + _emit(node.body) + " if " + _emit(node.test) + " else " + _emit(node.orelse) + ")"
    if isinstance(node, ast.Call):
        return 'h["' + node.func.id + '"](' + ", ".join(_emit(a) for a in node.args) + ")"
    if isinstance(node, ast.Name):
        return ('h["' if node.id in sp.HELPERS else 'n["') + node.id + '"]'
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.List):
        return "[" + ", ".join(_emit(e) for e in node.elts) + "]"
    if isinstance(node, ast.Tuple):
        return "(" + "".join(_emit(e) + ", " for e in node.elts) + ")"
    raise ExprError(type(node).__name__ + " is not allowed")


def expressions(spec):
    """{path: text} for every expression the spec sets (not_specified ones are left out)."""
    out = {}
    for pattern in sp.EXPRESSIONS:
        for path, text in sp.expand(spec, pattern):
            if isinstance(text, str) and text != sp.NS:
                out[path] = text
    return out


def sources(spec):
    return {path: to_source(text) for path, text in expressions(spec).items()}


def compile_source(source):
    return eval("lambda n, h: " + source, {"__builtins__": {}})  # builder side only; the brain embeds the source


def compile_all(spec):
    return {path: compile_source(src) for path, src in sources(spec).items()}


# ---------- synthetic inputs ----------

def item_schema(spec, api):
    """(products path, {item field: type}) of the lookup tool's product rows, from the bindings and the tool API."""
    b = (spec.get("bindings") or {})
    name = (b.get("tools") or {}).get("lookup")
    lst = ((b.get("response") or {}).get("lookup") or {}).get("products")
    tool = api.tools.get(name) if name else None
    if not tool or not isinstance(lst, str):
        return lst or "", {}
    pre = lst + "[]."
    return lst, {p[len(pre):]: (f.type or "string") for p, f in tool.response.items()
                 if p.startswith(pre) and "[]" not in p[len(pre):]}


def synthetic_nows(spec, today=None):
    """Clock values around today and every holiday: Fridays, weekends, holiday eves, early and after-hours times."""
    today = today or datetime.date.today()
    anchors = [today]
    for h in (spec.get("calendar") or {}).get("holidays") or []:
        try:
            anchors.append(datetime.date.fromisoformat(h))
        except (TypeError, ValueError):
            pass
    days = sorted({a + datetime.timedelta(days=k) for a in anchors for k in range(-8, 9)})
    return [datetime.datetime.combine(d, datetime.time(hh, mm)) for d in days for hh, mm in ((7, 30), (11, 0), (21, 45))]


def synthetic_products(spec, api):
    items = (((spec.get("bindings") or {}).get("response") or {}).get("lookup") or {}).get("item") or {}
    _, fields = item_schema(spec, api)
    kinds = [p.get("kind") for p in (spec.get("disclosure") or {}).get("product_names") or [] if isinstance(p, dict)]
    kind_field = items.get("kind")
    if kind_field and api.tools.get((spec.get("bindings") or {}).get("tools", {}).get("lookup", "")):
        lst = spec["bindings"]["response"]["lookup"].get("products")
        f = api.tools[spec["bindings"]["tools"]["lookup"]].response.get(f"{lst}[].{kind_field}")
        if f is not None and f.enum:
            kinds = list(dict.fromkeys(kinds + list(f.enum)))
    out = []
    for kind in kinds or ["product"]:
        for due in (1.0, 37.5, 420.0, 12345.67):
            for days in (1, 3, 7, 8, 15, 30, 45, 60, 120):
                raw = {}
                for name, typ in fields.items():
                    if typ == "number":
                        raw[name] = round(due * 0.3, 2)
                    elif typ == "integer":
                        raw[name] = 3
                    elif typ == "boolean":
                        raw[name] = False
                    else:
                        raw[name] = "1234"
                for role, value in (("ref", "p1"), ("kind", kind), ("due_amount", due), ("days_overdue", days)):
                    if items.get(role):
                        raw[items[role]] = value
                out.append({"ref": "p1", "kind": kind, "due_amount": due, "days_overdue": days, "raw": raw})
    return out


def _type_error(kind, v):
    if kind == "bool":
        return None if isinstance(v, bool) else f"returns {type(v).__name__} {v!r}, expected true or false"
    if kind == "amount":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"returns {type(v).__name__} {v!r}, expected an amount"
        return None if v >= 0 else f"returns {v!r}, expected an amount of 0 or more"
    if kind == "date":
        if isinstance(v, datetime.datetime) or not isinstance(v, datetime.date):
            return f"returns {type(v).__name__} {v!r}, expected a date"
        return None
    return None


def _where(now, extra):
    s = f"now={now:%Y-%m-%dT%H:%M} ({now:%A})"
    return s + ("" if not extra else ", " + ", ".join(f"{k}={v}" for k, v in extra.items()))


def check_values(spec, api, today=None, skip=()):
    """One error per expression that raises or returns the wrong type on a synthetic input."""
    cal = pl.calendar((spec.get("calendar") or {}).get("business_weekdays"), (spec.get("calendar") or {}).get("holidays"))
    h = pl.helpers(cal)
    nows = synthetic_nows(spec, today)
    products = synthetic_products(spec, api)
    _, fields = item_schema(spec, api)
    errs = []
    for path, text in expressions(spec).items():
        if path in skip:
            continue
        pattern = next((p for p in sp.EXPRESSIONS if any(q == path for q, _ in sp.expand(spec, p))), None)
        kind = sp.EXPR_TYPES.get(pattern)
        try:
            f = compile_source(to_source(text))
        except Exception as e:  # noqa: BLE001 - reported as a validation error
            errs.append(f"{path}: cannot be compiled ({e})")
            continue
        names = sp.EXPRESSIONS.get(pattern, ())
        err = _run(f, kind, nows, products, fields, "d" in names, sp.ITEM_FIELDS in names or "due_amount" in names, h)
        if err:
            errs.append(f"{path}: {err}")
    return errs


def _run(f, kind, nows, products, fields, with_d, with_product, h):
    for now in nows:
        today = now.date()
        base = {"today": today, "now": now}
        ds = [today + datetime.timedelta(days=k) for k in range(-2, 22)] if with_d else [None]
        prods = products if with_product else [None]
        for d in ds:
            for p in prods:
                n = dict(base)
                extra = {}
                if d is not None:
                    n["d"] = d
                    extra["d"] = d.isoformat()
                if p is not None:
                    n.update({name: None for name in fields})
                    n.update(p["raw"])
                    n.update({k: p[k] for k in ("ref", "kind", "due_amount", "days_overdue")})
                    extra.update({k: p[k] for k in ("kind", "due_amount", "days_overdue")})
                try:
                    v = f(n, h)
                except Exception as e:  # noqa: BLE001
                    return f"raises {type(e).__name__}: {e} on {_where(now, extra)}"
                bad = _type_error(kind, v)
                if bad:
                    return f"{bad} on {_where(now, extra)}"
    return None
