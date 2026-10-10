"""Bindings: how the engine's generic roles map onto one bank's tool API.

The engine never names a tool, a body field or a response field. It speaks in roles:

- tool roles (`TOOL_ROLES`): verify, lookup, record_promise, record_note, book_callback, transfer, close;
- call-variable roles (`CALL_VAR_ROLES`): customer_key, clock, given_name, family_name, honorific, company;
- request roles per tool role: the values the engine produces (question, answer, amount, date, ...);
- response roles per tool role: what the engine reads back (ok, verified, attempts_left, products, ...).

A binding set (part of the spec, written from the brief and the tool API file) maps each role to a tool name, a body
field or a response path. Bodies are rendered from roles with converters (cents, joined date-times, ...); the stub
bank parses them back with the inverse converters.

    {"call_vars": {role: call variable name},
     "tools":     {tool role: tool name},
     "request":   {tool role: {body field path: {"source": "call_var"|"engine"|"literal"|"template",
                                                 "value": ..., "convert": converter (optional)}}},
     "response":  {tool role: {response role: path, "item": {item role: path inside one list item}}}}
"""
import dataclasses, datetime, re
from decimal import ROUND_HALF_UP, Decimal

from builder import toolapi as ta

TOOL_ROLES = {
    "verify": {"request": ("question", "answer"), "response": ("verified", "all_verified", "attempts_left", "hint")},
    "lookup": {"request": (), "response": ("products",), "item": ("ref", "kind", "due_amount", "days_overdue")},
    "record_promise": {"request": ("product_ref", "amount", "date"), "response": ("promise_ref",)},
    "record_note": {"request": ("product_ref", "note_reason", "note_text"), "response": ()},
    "book_callback": {"request": ("date", "time"), "response": ()},
    "transfer": {"request": ("queue",), "response": ()},
    "close": {"request": ("outcome", "reason"), "response": ()},
}
COMMON_RESPONSE = ("ok", "error")
CALL_VAR_ROLES = ("customer_key", "clock", "given_name", "family_name", "honorific", "company")
SOURCES = ("call_var", "engine", "literal", "template")
# Engine roles a tool role cannot work without (each must reach the body, alone or through a join).
ESSENTIAL = {"verify": ("answer",), "record_promise": ("product_ref", "amount", "date"),
             "record_note": ("product_ref",), "book_callback": ("date",), "close": ("outcome",)}
NUMBER_ROLES = ("amount",)
RESPONSE_TYPES = {"ok": ("boolean",), "verified": ("boolean",), "all_verified": ("boolean",),
                  "attempts_left": ("integer", "number"), "hint": ("string",), "error": ("string",),
                  "products": ("array",), "promise_ref": ("string", "integer")}
ITEM_TYPES = {"ref": ("string", "integer"), "kind": ("string",), "due_amount": ("number", "integer"),
              "days_overdue": ("integer", "number")}
_TEMPLATE_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class BindingError(Exception):
    pass


# ---------- converters ----------

def _d(x):
    return Decimal(str(x))


def number_2dp(x):
    return float(_d(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def cents_int(x):
    return int((_d(x) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def string_2dp(x):
    return str(_d(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def date_iso(x):
    if isinstance(x, (datetime.date, datetime.datetime)):
        return x.strftime("%Y-%m-%d")
    return datetime.date.fromisoformat(str(x)[:10]).isoformat()


def time_hhmm(x):
    if isinstance(x, (datetime.time, datetime.datetime)):
        return x.strftime("%H:%M")
    m = re.fullmatch(r"\s*(\d{1,2})[:.h](\d{2})(?::\d{2})?\s*", str(x))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise BindingError(f"not a time: {x!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def datetime_join(d, t):
    return f"{date_iso(d)}T{time_hhmm(t)}:00"


def _split_datetime(v):
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2})?.*", str(v))
    if not m:
        raise BindingError(f"not a date-time: {v!r}")
    return m.group(1), m.group(2)


# name: (forward, inverse, input kind, output types)
CONVERTERS = {
    "number_2dp": (number_2dp, float, "number", ("number",)),
    "cents_int": (cents_int, lambda v: float(_d(v) / 100), "number", ("integer", "number")),
    "string_2dp": (string_2dp, float, "number", ("string",)),
    "date_iso": (date_iso, str, "date", ("string",)),
    "time_hhmm": (time_hhmm, str, "time", ("string",)),
    "datetime_join": (datetime_join, _split_datetime, "date+time", ("string",)),
}


# ---------- model ----------

@dataclasses.dataclass
class Bindings:
    call_vars: dict
    tools: dict
    request: dict
    response: dict

    @classmethod
    def from_dict(cls, d):
        return cls(call_vars=dict(d.get("call_vars") or {}), tools=dict(d.get("tools") or {}),
                   request={k: dict(v) for k, v in (d.get("request") or {}).items()},
                   response={k: dict(v) for k, v in (d.get("response") or {}).items()})

    def to_dict(self):
        return dataclasses.asdict(self)

    def role_of(self, tool_name):
        return next((r for r, n in self.tools.items() if n == tool_name), None)

    def engine_fields(self, role):
        """{engine role: (body path, converter)} for the engine-sourced fields of a tool role."""
        out = {}
        for path, b in (self.request.get(role) or {}).items():
            if b.get("source") == "engine":
                for r in (b["value"] if isinstance(b["value"], list) else [b["value"]]):
                    out[r] = (path, b.get("convert"))
        return out


# ---------- validation ----------

def _types_of(field):
    return {field.type} if field.type else set()


def validate(b, api, call_vars=None):
    """Every error of the binding set against the tool API. `call_vars` (names) checks call-variable bindings."""
    errs = []
    for role, name in b.call_vars.items():
        if role not in CALL_VAR_ROLES:
            errs.append(f"call_vars.{role}: unknown role (one of {', '.join(CALL_VAR_ROLES)})")
        if not isinstance(name, str) or not name:
            errs.append(f"call_vars.{role}: needs a variable name")
        elif call_vars is not None and name not in call_vars:
            errs.append(f"call_vars.{role}: {name!r} is not a call variable ({', '.join(sorted(call_vars))})")
    seen = {}
    for role, name in b.tools.items():
        if role not in TOOL_ROLES:
            errs.append(f"tools.{role}: unknown tool role (one of {', '.join(TOOL_ROLES)})")
        if name not in api.tools:
            errs.append(f"tools.{role}: no tool {name!r} in the tool API ({', '.join(api.tools)})")
        if name in seen:
            errs.append(f"tools.{role}: tool {name!r} is already bound to {seen[name]}")
        seen[name] = role
    for role in set(b.request) | set(b.response):
        if role not in b.tools:
            errs.append(f"{role}: bound fields but no tool (add tools.{role})")
    for role, name in b.tools.items():
        if role in TOOL_ROLES and name in api.tools:
            errs += _validate_request(b, role, api.tools[name])
            errs += _validate_response(b, role, api.tools[name])
    return errs


def _validate_request(b, role, tool):
    errs, fields, engine_roles = [], b.request.get(role) or {}, set()
    for path, bind in fields.items():
        where = f"request.{role}.{path}"
        f = tool.request.get(path)
        if f is None:
            errs.append(f"{where}: no such body field in {tool.name} ({', '.join(tool.request) or 'no body'})")
            continue
        if f.type == "object":
            errs.append(f"{where}: bind the fields inside the object, not the object")
            continue
        src, val, conv = bind.get("source"), bind.get("value"), bind.get("convert")
        if src not in SOURCES:
            errs.append(f"{where}: source must be one of {', '.join(SOURCES)}")
            continue
        if conv is not None and conv not in CONVERTERS:
            errs.append(f"{where}: unknown converter {conv!r} ({', '.join(CONVERTERS)})")
            continue
        if src == "call_var":
            if val not in b.call_vars:
                errs.append(f"{where}: call-variable role {val!r} is not bound in call_vars")
        elif src == "literal":
            errs += [f"{where}: literal {e.split(': ', 1)[1]}" for e in ta.check(_field_schema(tool, path), val, path)]
        elif src == "template":
            if not isinstance(val, str):
                errs.append(f"{where}: a template is a string")
            elif f.type not in ("string", ""):
                errs.append(f"{where}: a template fills only a string field")
            else:
                allowed = set(CALL_VAR_ROLES) | set(TOOL_ROLES[role]["request"])
                for name in _TEMPLATE_VAR.findall(val):
                    if name not in allowed:
                        errs.append(f"{where}: template variable {name!r} is not a role ({', '.join(sorted(allowed))})")
        elif src == "engine":
            names = val if isinstance(val, list) else [val]
            bad = [n for n in names if n not in TOOL_ROLES[role]["request"]]
            if bad or not names:
                errs.append(f"{where}: engine role {bad or val!r} is not one of {', '.join(TOOL_ROLES[role]['request'])}")
                continue
            engine_roles |= set(names)
            errs += _check_conversion(where, names, conv, f)
    for path, f in tool.request.items():
        if f.required and f.type != "object" and "[]" not in path and path not in fields:
            errs.append(f"request.{role}.{path}: required body field of {tool.name} has no binding")
    for need in ESSENTIAL.get(role, ()):
        if need not in engine_roles:
            errs.append(f"request.{role}: engine role {need!r} reaches no body field")
    return errs


def _check_conversion(where, names, conv, f):
    if len(names) > 1 and conv != "datetime_join":
        return [f"{where}: several engine roles need the datetime_join converter"]
    if conv is None:
        kind = "number" if names[0] in NUMBER_ROLES else "string"
        if f.type and f.type != kind:
            hint = " (use cents_int)" if f.type == "integer" and kind == "number" else ""
            return [f"{where}: engine role {names[0]!r} gives a {kind}, the field is {f.type}{hint}"]
        return []
    _, _, kind, out_types = CONVERTERS[conv]
    errs = []
    if kind == "date+time" and sorted(names) != ["date", "time"]:
        errs.append(f"{where}: datetime_join takes the engine roles ['date', 'time']")
    elif kind == "number" and names[0] not in NUMBER_ROLES:
        errs.append(f"{where}: {conv} needs a number role ({', '.join(NUMBER_ROLES)})")
    elif kind in ("date", "time") and names[0] != kind:
        errs.append(f"{where}: {conv} needs the engine role {kind!r}")
    if f.type and f.type not in out_types:
        errs.append(f"{where}: {conv} gives {'/'.join(out_types)}, the field is {f.type}")
    return errs


def _field_schema(tool, path):
    node = tool.request_schema
    for part in path.replace("[]", ".[]").split("."):
        if not part:
            continue
        node = (node.get("items") or {}) if part == "[]" else (node.get("properties") or {}).get(part, {})
    return node


def _validate_response(b, role, tool):
    errs, resp = [], b.response.get(role) or {}
    allowed = set(COMMON_RESPONSE) | set(TOOL_ROLES[role]["response"]) | ({"item"} if "item" in TOOL_ROLES[role] else set())
    for r, path in resp.items():
        where = f"response.{role}.{r}"
        if r not in allowed:
            errs.append(f"{where}: unknown response role (one of {', '.join(sorted(allowed))})")
        elif r == "item":
            continue
        elif path not in tool.response:
            errs.append(f"{where}: no response field {path!r} in {tool.name} ({', '.join(tool.response) or 'no schema'})")
        elif tool.response[path].type and tool.response[path].type not in RESPONSE_TYPES[r]:
            errs.append(f"{where}: {path} is {tool.response[path].type}, the role needs {'/'.join(RESPONSE_TYPES[r])}")
    if "item" in TOOL_ROLES[role]:
        if "products" not in resp:
            errs.append(f"response.{role}.products: the product list has no binding")
        items = resp.get("item") or {}
        if "ref" not in items:
            errs.append(f"response.{role}.item.ref: the product id has no binding")
        for r, sub in items.items():
            where, full = f"response.{role}.item.{r}", f"{resp.get('products')}[].{sub}"
            if r not in TOOL_ROLES[role]["item"]:
                errs.append(f"{where}: unknown item role (one of {', '.join(TOOL_ROLES[role]['item'])})")
            elif full not in tool.response:
                errs.append(f"{where}: no field {sub!r} in the items of {resp.get('products')}")
            elif tool.response[full].type and tool.response[full].type not in ITEM_TYPES[r]:
                errs.append(f"{where}: {sub} is {tool.response[full].type}, the role needs {'/'.join(ITEM_TYPES[r])}")
    return errs


# ---------- bodies ----------

def _get(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _set(obj, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = value


def render_body(b, role, engine, call_values):
    """The request body for a tool role from engine values and the call's variables."""
    body = {}
    for path, bind in (b.request.get(role) or {}).items():
        src, val, conv = bind.get("source"), bind.get("value"), bind.get("convert")
        if src == "literal":
            v = val
        elif src == "call_var":
            v = call_values.get(b.call_vars.get(val, ""))
        elif src == "template":
            values = {**{r: call_values.get(n) for r, n in b.call_vars.items()}, **engine}
            v = _TEMPLATE_VAR.sub(lambda m: "" if values.get(m.group(1)) is None else str(values[m.group(1)]), val)
        else:
            names = val if isinstance(val, list) else [val]
            args = [engine.get(n) for n in names]
            if any(a is None for a in args):
                v = None
            elif conv:
                v = CONVERTERS[conv][0](*args)
            else:
                v = args[0]
        if v is None:
            continue  # an unset optional value; a missing required one shows in the request check
        _set(body, path, v)
    return body


def parse_body(b, role, body):
    """Engine values back from a rendered body (the stub bank's view of a request)."""
    out = {}
    for path, bind in (b.request.get(role) or {}).items():
        if bind.get("source") != "engine":
            continue
        v = _get(body, path)
        if v is None:
            continue
        names = bind["value"] if isinstance(bind["value"], list) else [bind["value"]]
        conv = bind.get("convert")
        v = CONVERTERS[conv][1](v) if conv else v
        if len(names) > 1:
            out.update(zip(names, v))
        else:
            out[names[0]] = v
    return out


def build_response(b, role, values, items=None):
    """A response body from role values; `items` are product dicts in item roles plus raw API fields."""
    resp, body = b.response.get(role) or {}, {}
    for r, v in values.items():
        if r in resp and r != "item" and v is not None:
            _set(body, resp[r], v)
    if items is not None and "products" in resp:
        rows = []
        for it in items:
            row = dict(it.get("fields") or {})
            for r, sub in (resp.get("item") or {}).items():
                if it.get(r) is not None:
                    _set(row, sub, it[r])
            rows.append(row)
        _set(body, resp["products"], rows)
    return body


def read_response(b, role, body):
    """Role values from a response body; products come back as item-role dicts with the raw row kept."""
    resp, out = b.response.get(role) or {}, {}
    for r, path in resp.items():
        if r not in ("item", "products"):
            out[r] = _get(body or {}, path)
    if "products" in resp:
        rows = _get(body or {}, resp["products"]) or []
        out["products"] = [{**{r: _get(row, sub) for r, sub in (resp.get("item") or {}).items()}, "raw": row}
                           for row in rows]
    return out
