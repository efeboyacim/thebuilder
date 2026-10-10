"""Stage 2, ToolAPI: the bank's tools, parsed from the openapi file without an LLM.

Per tool: method, path, summary, the request body schema and its fields, the success response schema and its fields,
and the error statuses. `$ref`, `allOf` and nullable unions are resolved. Fields are flattened to dotted paths; an
array's items are written `<path>[].<field>`. `check()` validates a value against a resolved schema; the stub bank and
the bindings validator use it.
"""
import dataclasses, datetime, re

METHODS = ("get", "post", "put", "patch", "delete")
MAX_DEPTH = 40


class ToolAPIError(Exception):
    pass


@dataclasses.dataclass
class Field:
    path: str
    type: str  # string, integer, number, boolean, array, object, or "" when the schema does not say
    required: bool
    enum: list = None
    format: str = ""
    description: str = ""
    nullable: bool = False

    def describe(self):
        bits = [self.type or "any"]
        if self.format:
            bits.append(f"format {self.format}")
        if self.required:
            bits.append("required")
        if self.enum:
            bits.append("enum [" + ", ".join(map(str, self.enum)) + "]")
        if self.nullable:
            bits.append("nullable")
        text = f"{self.path}: {', '.join(bits)}"
        return text + (f" -- {self.description}" if self.description else "")


@dataclasses.dataclass
class Tool:
    name: str
    method: str
    path: str
    summary: str
    description: str
    request_schema: dict  # resolved; {} when the operation has no body
    request: dict  # path -> Field
    response_schema: dict  # resolved success schema; {} when none is given
    response: dict  # path -> Field
    success_status: str
    error_statuses: dict  # status -> description
    parameters: list  # non-body parameters: {"name", "in", "required", "schema"}


@dataclasses.dataclass
class ToolAPI:
    server: str
    tools: dict  # name -> Tool
    title: str = ""
    description: str = ""

    def url(self, tool_name):
        return self.server.rstrip("/") + self.tools[tool_name].path


# ---------- schema resolution ----------

def _pointer(root, ref):
    if not ref.startswith("#/"):
        raise ToolAPIError(f"external $ref not supported: {ref}")
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ToolAPIError(f"$ref not found: {ref}")
        node = node[part]
    return node


def resolve(node, root, depth=0):
    """A copy of the schema with $ref, allOf and nullable unions resolved."""
    if depth > MAX_DEPTH:
        raise ToolAPIError("schema nesting too deep (recursive $ref?)")
    if isinstance(node, list):
        return [resolve(n, root, depth + 1) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = resolve(_pointer(root, node["$ref"]), root, depth + 1)
        extra = {k: v for k, v in node.items() if k != "$ref"}
        return {**target, **resolve(extra, root, depth + 1)} if extra else target
    out = {k: resolve(v, root, depth + 1) for k, v in node.items()}
    if "allOf" in out:
        merged = {k: v for k, v in out.items() if k != "allOf"}
        for part in out["allOf"]:
            for k, v in part.items():
                if k == "properties":
                    merged.setdefault("properties", {}).update(v)
                elif k == "required":
                    merged["required"] = sorted(set(merged.get("required", [])) | set(v))
                else:
                    merged.setdefault(k, v)
        out = merged
    for key in ("oneOf", "anyOf"):
        alts = out.get(key)
        if isinstance(alts, list) and alts:
            real = [a for a in alts if a.get("type") != "null"]
            base = {k: v for k, v in out.items() if k != key}
            if len(real) == 1:  # a nullable union, or a single alternative
                out = {**real[0], **base}
                if len(real) < len(alts):
                    out["nullable"] = True
    t = out.get("type")
    if isinstance(t, list):  # OpenAPI 3.1: ["string", "null"]
        types = [x for x in t if x != "null"]
        out["type"] = types[0] if len(types) == 1 else ""
        if "null" in t:
            out["nullable"] = True
    return out


def flatten(schema, prefix="", required=True, out=None):
    """{path: Field} for every property, nested objects and array items included."""
    out = {} if out is None else out
    if schema.get("type") == "array" or "items" in schema:
        items = schema.get("items") or {}
        if items.get("type") == "object" or "properties" in items:
            flatten(items, prefix + "[].", required, out)
        return out
    req = set(schema.get("required") or [])
    for name, prop in (schema.get("properties") or {}).items():
        path = prefix + name
        typ = prop.get("type") or ("object" if "properties" in prop else "array" if "items" in prop else "")
        out[path] = Field(path=path, type=typ, required=required and name in req, enum=prop.get("enum"),
                          format=prop.get("format", ""), description=(prop.get("description") or "").strip(),
                          nullable=bool(prop.get("nullable")))
        if typ in ("object", "array"):
            flatten(prop, path + ("." if typ == "object" else ""), required and name in req, out)
    return out


def _json_content(content):
    if not isinstance(content, dict) or not content:
        return {}
    for ct, body in content.items():
        if "json" in ct:
            return body or {}
    return next(iter(content.values())) or {}


def parse(data):
    """ToolAPI from a parsed openapi document."""
    if not isinstance(data, dict) or "openapi" not in data:
        raise ToolAPIError("not an openapi document")
    servers = [s for s in data.get("servers") or [] if isinstance(s, dict) and s.get("url")]
    server = ""
    if servers:
        server = servers[0]["url"]
        for name, var in (servers[0].get("variables") or {}).items():
            server = server.replace("{" + name + "}", str(var.get("default", "")))
    tools = {}
    for path, item in (data.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        item = resolve(item, data)
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or path.strip("/").replace("/", "_") or method
            if name in tools:
                raise ToolAPIError(f"two operations named {name}")
            body = op.get("requestBody") or {}
            req_schema = _json_content(body.get("content")).get("schema") or {}
            statuses = {str(k): v for k, v in (op.get("responses") or {}).items()}
            ok_codes = sorted(k for k in statuses if k.startswith("2")) or (["default"] if "default" in statuses else [])
            success = ok_codes[0] if ok_codes else ""
            resp_schema = _json_content((statuses.get(success) or {}).get("content")).get("schema") or {}
            params = [{"name": p.get("name"), "in": p.get("in"), "required": bool(p.get("required")),
                       "schema": p.get("schema") or {}}
                      for p in (item.get("parameters") or []) + (op.get("parameters") or []) if isinstance(p, dict)]
            tools[name] = Tool(
                name=name, method=method.upper(), path=path, summary=(op.get("summary") or "").strip(),
                description=(op.get("description") or "").strip(), request_schema=req_schema,
                request=flatten(req_schema, required=True), response_schema=resp_schema,
                response=flatten(resp_schema, required=True), success_status=success,
                error_statuses={k: (v or {}).get("description", "") for k, v in statuses.items()
                                if k != success and not k.startswith("2")},
                parameters=params)
    if not tools:
        raise ToolAPIError("the openapi document has no operations")
    info = data.get("info") or {}
    return ToolAPI(server=server, tools=tools, title=info.get("title", ""), description=info.get("description", ""))


# ---------- validation ----------

def _format_ok(fmt, value):
    try:
        if fmt == "date":
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) and bool(datetime.date.fromisoformat(value))
        if fmt == "date-time":
            datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "T" in value or " " in value
        if fmt == "time":
            return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?", value))
    except ValueError:
        return False
    return True  # formats we do not check


def _type_ok(typ, value):
    if typ == "string":
        return isinstance(value, str)
    if typ == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "array":
        return isinstance(value, list)
    if typ == "object":
        return isinstance(value, dict)
    return True


def check(schema, value, path="body"):
    """Errors of value against a resolved schema: type, required, enum, format, pattern, bounds, extra keys."""
    errs = []
    if value is None:
        return [] if schema.get("nullable") else [f"{path}: null is not allowed"]
    typ = schema.get("type") or ("object" if "properties" in schema else "array" if "items" in schema else "")
    if not _type_ok(typ, value):
        return [f"{path}: expected {typ}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if schema.get("format") and not _format_ok(schema["format"], value):
            errs.append(f"{path}: {value!r} is not a valid {schema['format']}")
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            errs.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errs.append(f"{path}: length {len(value)} out of bounds")
    if typ in ("number", "integer") and isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if typ == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errs.append(f"{path}.{name}: required field missing")
        for name, v in value.items():
            if name in props:
                errs += check(props[name], v, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{name}: field not in the schema")
    if typ == "array" and isinstance(value, list) and schema.get("items"):
        for i, v in enumerate(value):
            errs += check(schema["items"], v, f"{path}[{i}]")
    return errs


def validate_body(tool, body):
    if not tool.request_schema:
        return [] if body in (None, {}) else ["body: this tool takes no body"]
    return check(tool.request_schema, body, "body")


def unknown_fields(tool, body):
    """Top-level body keys the schema does not declare (allowed unless additionalProperties is false)."""
    props = tool.request_schema.get("properties") or {}
    return sorted(k for k in (body or {}) if k not in props)


def example(schema):
    """A schema-shaped example value: first enum value, typed placeholders, every property filled."""
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    typ = schema.get("type") or ("object" if "properties" in schema else "array" if "items" in schema else "")
    fmt = schema.get("format", "")
    if typ == "object":
        return {k: example(v) for k, v in (schema.get("properties") or {}).items()}
    if typ == "array":
        return [example(schema.get("items") or {})]
    if typ == "string":
        return {"date": "2000-01-03", "date-time": "2000-01-03T10:00:00", "time": "10:00"}.get(fmt, "text")
    return {"integer": 1, "number": 1.5, "boolean": True}.get(typ, "text")


def summary_text(api):
    """A compact, complete description of the tools for the spec prompt."""
    lines = [f"server: {api.server}"]
    if api.description:
        lines.append(f"about: {api.description}")
    for t in api.tools.values():
        lines.append(f"\ntool {t.name} ({t.method} {t.path}): {t.summary}")
        if t.description:
            lines.append(f"  description: {t.description}")
        for p in t.parameters:
            lines.append(f"  parameter {p['name']} in {p['in']}{', required' if p['required'] else ''}")
        lines.append("  request body:" if t.request else "  request body: none")
        lines += [f"    {f.describe()}" for f in t.request.values()]
        lines.append(f"  response {t.success_status}:" if t.response else f"  response {t.success_status}: no schema")
        lines += [f"    {f.describe()}" for f in t.response.values()]
        for code, desc in t.error_statuses.items():
            lines.append(f"  status {code}: {desc}")
    return "\n".join(lines)
