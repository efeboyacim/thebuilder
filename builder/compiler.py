"""Compile a validated spec and the tool API into a Freya workflow: the hub-and-spoke brain (docs/design.md §3).

    wf = compile_workflow(spec, api)      # the workflow JSON (no LLM)
    lint(wf)                              # graph and code errors; [] when clean
    summary(wf)                           # node and tool counts for the trace

Graph: init (code) -> opening (tts) -> brain (code) -> talk_<stage> (tts {{say_next}}) | api_<role> (api_request)
| end_call (farewell {{farewell}}); every talk and api node returns to the brain. The brain's first transition takes a
failed code run to talk_error, its last `always` takes an unmatched route to talk_fallback. The close tool is a
workflow-level lifecycle tool whose pre-hook rebuilds outcome and reason from final_outcome/final_reason.
Every name the brain uses comes from builder/engine.py; nothing here knows a brief.
"""
import copy, json

from builder import bindings as bd
from builder import brain, contract, engine

MIN_CONFIDENCE = 0.3
SAY_AGAIN = "I am sorry, could you say that again?"
SYSTEM_PROMPT = ("You are the bank's phone agent. In every turn, say exactly the text your current instructions give "
                 "you, word for word, and nothing else. Never call a tool.")
TALK_TASK = "Say exactly this text, word for word, and nothing else: {{say_next}}. No tool call is allowed."


class CompileError(Exception):
    pass


# ---------- node shapes (from the spikes, docs/spikes.md) ----------

def always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def when(var, operand, target, op="equals"):
    return {"target_node": target, "description": f"{var} {op} {operand}",
            "condition": {"logic": "or", "conditions": [{"variable": var, "operator": op, "operand": operand}]}}


def code_node(name, code, transitions):
    fn = {"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec", "parameters": {
        "type": "object", "properties": {"code": {"type": "string", "default": code},
                                         "timeout_ms": {"type": "number", "default": 10000}}}}}
    return {"name": name, "node_type": "code", "passthrough": True,
            "passthrough_default_transition": transitions[-1]["target_node"], "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True, "pre_actions": [fn], "transitions": transitions}


def extraction_schema(fields):
    out = []
    for f in fields:
        item = {"name": f["name"], "type": f["type"], "description": f["description"],
                "min_confidence": MIN_CONFIDENCE}
        if f.get("enum"):
            item["enum_values"] = list(f["enum"])
        out.append(item)
    return out


def talk_node(name, text, task, fields):
    return {"name": name, "node_type": "conversation", "on_enter": {"type": "tts", "text": text},
            "task_message": task, "extraction_schema": extraction_schema(fields), "max_node_turns": 200,
            "disable_inactivity_monitor": True, "transitions": [always("brain")]}


def union_fields(contract_fields):
    """One field list for the talk nodes that serve every stage (fallback, error): enums merged."""
    merged = {}
    for fields in contract_fields.values():
        for f in fields:
            m = merged.setdefault(f["name"], dict(f, enum=list(f["enum"]) if f.get("enum") else None))
            if f.get("enum") and m.get("enum") is not None:
                m["enum"] += [v for v in f["enum"] if v not in m["enum"]]
                if f["description"] not in m["description"]:
                    m["description"] += " " + f["description"]
    return list(merged.values())


# ---------- api tools ----------

def _prop(field, default):
    p = {"type": field.type or "string", "default": default}
    if field.description:
        p["description"] = field.description[:300]
    if field.enum:
        p["enum"] = list(field.enum)
    return p


def _place(props, path, prop):
    parts = path.split(".")
    for part in parts[:-1]:
        node = props.setdefault(part, {"type": "object", "properties": {}})
        props = node["properties"]
    props[parts[-1]] = prop


def api_tool(api, b, role, call_var_names, defaults=None, timeout_ms=10000):
    """The api_call tool of one tool role. Body fields: literals baked, call variables as {{name}}, engine and
    template fields as the brain's {{a_<role>__<field>}} (or `defaults` per field path)."""
    tool = api.tools[b.tools[role]]
    props = {}
    for path, bind in (b.request.get(role) or {}).items():
        field = tool.request.get(path)
        if field is None:
            raise CompileError(f"bindings.request.{role}.{path}: no such body field in {tool.name}")
        src = bind.get("source")
        if src == "literal":
            default = bind.get("value")
        elif src == "call_var":
            default = "{{" + call_var_names[bind.get("value")] + "}}"
        elif defaults and path in defaults:
            default = defaults[path]
        else:
            default = "{{" + engine.arg_var(role, path) + "}}"
        _place(props, path, _prop(field, default))
    required = [p for p, f in tool.request.items() if f.required and "." not in p and "[]" not in p and p in props]
    params = {"url": {"type": "string", "default": api.server.rstrip("/") + tool.path},
              "method": {"type": "string", "default": (tool.method or "POST").upper()},
              "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
              "body": {"type": "object", "properties": props, "required": required}}
    return {"type": "function", "config": {"timeoutMs": timeout_ms, "maxRetries": 0},
            "function": {"name": tool.name, "handler": "__function__:api_call",
                         "description": (tool.description or tool.name)[:300],
                         "parameters": {"type": "object", "properties": params}}}


def api_node(api, b, role, call_var_names):
    name = engine.api_node(role)
    mapping = [{"path": "data." + path, "variable": engine.resp_var(role, r)}
               for r, path in (b.response.get(role) or {}).items() if r not in ("item", "products")]
    return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "brain",
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [api_tool(api, b, role, call_var_names)], "response_mapping": mapping,
            "transitions": [always("brain")]}


def close_hook(out_field, reason_field, default_outcome, default_reason):
    """Pre-hook of the lifecycle close: outcome and reason from the call variables, never from a model fill."""
    return "\n".join([
        "try:",
        "    b = config.get('body')",
        "    if isinstance(b, dict):",
        "        o = str(vars.get('final_outcome') or '').strip()",
        "        r = str(vars.get('final_reason') or '').strip()",
        "        if not o:",
        f"            o, r = {default_outcome!r}, {default_reason!r}",
        f"        b[{out_field!r}] = o",
        "        if r and r != '-':",
        f"            b[{reason_field!r}] = r",
        "        else:",
        f"            b.pop({reason_field!r}, None)",
        "except Exception:",
        "    pass",
        ""])


def close_tool(api, spec, b, call_var_names):
    fields = b.engine_fields("close")
    out_field = fields["outcome"][0]
    reason_field = fields.get("reason", (None,))[0]
    defaults = {out_field: "{{final_outcome}}"}
    if reason_field:
        defaults[reason_field] = "{{final_reason}}"
    tool = api_tool(api, b, "close", call_var_names, defaults=defaults, timeout_ms=12000)
    tool["config"]["alwaysRunsAt"] = ["end_of_call"]
    sid = spec["closing"]["hangup"]["open"]["situation"]
    sit = next(s for s in spec["situations"] if s["id"] == sid)
    tool["pre_process_code"] = close_hook(out_field, reason_field or "reason", sit["outcome"], sit["reason"] or "-")
    return tool


# ---------- the workflow ----------

def init_code(spec):
    sid = spec["closing"]["hangup"]["open"]["situation"]
    sit = next(s for s in spec["situations"] if s["id"] == sid)
    return "\n".join(["try:",
                      f"    vars['final_outcome'] = {sit['outcome']!r}",
                      f"    vars['final_reason'] = {(sit['reason'] or '-')!r}",
                      f"    vars['say_fallback'] = {SAY_AGAIN!r}",
                      "except Exception:",
                      "    pass", ""])


def compile_workflow(spec, api):
    b = bd.Bindings.from_dict(spec["bindings"])
    call_var_names = dict(b.call_vars)
    fields = contract.extraction(spec)
    code = brain.source(spec, api)
    roles = [r for r in ("verify", "lookup", "record_promise", "record_note", "book_callback", "transfer")
             if r in b.tools]
    nodes = {"init": code_node("init", init_code(spec), [always("opening")])}
    opening = spec["opening"]["template"]
    nodes["opening"] = talk_node("opening", opening, "Say exactly this text, word for word, and nothing else: "
                                 + opening + " No tool call is allowed.", fields["open"])
    targets = [engine.talk_node(s) for s in engine.STAGES] + [engine.api_node(r) for r in roles] + [engine.END]
    nodes["brain"] = code_node("brain", code, [when("brain_response.status", "error", "talk_error")]
                               + [when("route", t, t) for t in targets] + [always("talk_fallback")])
    for stage in engine.STAGES:
        name = engine.talk_node(stage)
        nodes[name] = talk_node(name, "{{say_next}}", TALK_TASK, fields[stage])
    every = union_fields(fields)
    nodes["talk_fallback"] = talk_node("talk_fallback", "{{say_fallback}}", TALK_TASK.replace("say_next", "say_fallback"), every)
    nodes["talk_error"] = talk_node("talk_error", SAY_AGAIN, f"Say exactly: {SAY_AGAIN} No tool call is allowed.", every)
    for role in roles:
        nodes[engine.api_node(role)] = api_node(api, b, role, call_var_names)
    nodes[engine.END] = {
        "name": engine.END, "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "transitions": [], "terminal": {"type": "function", "function": {
            "name": "end_call", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {
                "message_mode": {"type": "string", "default": "custom"},
                "spoken_message": {"type": "string", "default": "{{farewell}}"}}}}}}
    wf = {"initial_node": "init", "system_prompt": SYSTEM_PROMPT, "nodes": nodes, "global_nodes": {},
          "tools": [close_tool(api, spec, b, call_var_names)]}
    errs = lint(wf)
    if errs:
        raise CompileError("; ".join(errs[:10]))
    return wf


# ---------- checks ----------

def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def lint(wf):
    """Graph and code errors (docs/design.md §4.1): unknown targets, unreachable nodes, a passthrough node without a
    final always, two transitions to one target, a non-silent code or api node, tools or pre_actions on the wrong
    node type, null, const, doubled braces in code."""
    errs, nodes = [], wf.get("nodes") or {}
    if wf.get("initial_node") not in nodes:
        errs.append(f"initial node {wf.get('initial_node')!r} does not exist")
    for path, v in _walk(wf):
        if v is None:
            errs.append(f"{path}: null")
        if path.endswith(".const"):
            errs.append(f"{path}: const is never sent")
        if (path.endswith("code.default") or path.endswith("_process_code")) and isinstance(v, str) \
                and ("{{" in v or "}}" in v):
            errs.append(f"{path}: code contains a doubled brace")
    for name, n in nodes.items():
        if n.get("name") != name:
            errs.append(f"{name}: node name {n.get('name')!r} differs from its key")
        targets = [t.get("target_node") for t in n.get("transitions") or []]
        for t in targets:
            if t not in nodes:
                errs.append(f"{name}: transition to unknown node {t!r}")
        for t in sorted({t for t in targets if targets.count(t) > 1}):
            errs.append(f"{name}: two transitions to {t!r} (they merge)")
        typ = n.get("node_type")
        if typ in ("code", "api_request"):
            if (n.get("on_enter") or {}).get("type") != "silent":
                errs.append(f"{name}: a {typ} node must be silent")
            if n.get("tools"):
                errs.append(f"{name}: tools on a {typ} node are stripped")
        if typ == "conversation" and n.get("pre_actions"):
            errs.append(f"{name}: pre_actions on a conversation node are stripped")
        if n.get("passthrough"):
            last = (n.get("transitions") or [{}])[-1]
            if (last.get("condition") or {}).get("operator") != "always":
                errs.append(f"{name}: a passthrough node must end with an always transition")
        if typ == "end_call" and n.get("transitions"):
            errs.append(f"{name}: an end node has no transitions")
    seen, todo = set(), [wf.get("initial_node")]
    while todo:
        x = todo.pop()
        if x in seen or x not in nodes:
            continue
        seen.add(x)
        todo += [t.get("target_node") for t in nodes[x].get("transitions") or []]
    for name in nodes:
        if name not in seen:
            errs.append(f"{name}: unreachable")
    ends = {k for k, n in nodes.items() if n.get("node_type") == "end_call"}
    for name in seen:
        if not _reaches(nodes, name, ends):
            errs.append(f"{name}: no path to an end node")
    return errs


def _reaches(nodes, start, ends):
    seen, todo = set(), [start]
    while todo:
        x = todo.pop()
        if x in ends:
            return True
        if x in seen or x not in nodes:
            continue
        seen.add(x)
        todo += [t.get("target_node") for t in nodes[x].get("transitions") or []]
    return False


def summary(wf):
    nodes = wf.get("nodes") or {}
    types = {}
    for n in nodes.values():
        types[n.get("node_type")] = types.get(n.get("node_type"), 0) + 1
    code = next((n["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
                 for n in nodes.values() if n.get("name") == "brain"), "")
    return {"nodes": len(nodes), "node_types": types,
            "tools": {"lifecycle": sum(1 for t in wf.get("tools") or [] if (t.get("config") or {}).get("alwaysRunsAt")),
                      "pre_actions": sum(len(n.get("pre_actions") or []) for n in nodes.values()
                                         if n.get("node_type") == "api_request"),
                      "code": sum(1 for n in nodes.values() if n.get("node_type") == "code")},
            "brain_bytes": len(code.encode("utf-8"))}


def readback_diff(sent, got):
    """What the platform changed or dropped: per node, the keys and list lengths that differ."""
    diffs = []
    sn, gn = sent.get("nodes") or {}, (got or {}).get("nodes") or {}
    for name in sorted(set(sn) | set(gn)):
        if name not in gn:
            diffs.append(f"{name}: missing after save")
            continue
        if name not in sn:
            diffs.append(f"{name}: added by the platform")
            continue
        a, g = sn[name], gn[name]
        for k in ("node_type", "passthrough", "passthrough_default_transition", "initial"):
            if k in a and a.get(k) != g.get(k):
                diffs.append(f"{name}.{k}: sent {a.get(k)!r}, stored {g.get(k)!r}")
        for k in ("pre_actions", "tools", "extraction_schema", "transitions", "response_mapping"):
            if len(a.get(k) or []) != len(g.get(k) or []):
                diffs.append(f"{name}.{k}: sent {len(a.get(k) or [])}, stored {len(g.get(k) or [])}")
        if a.get("pre_actions") and g.get("pre_actions"):
            ca = json.dumps(a["pre_actions"][0].get("function", {}).get("parameters"), sort_keys=True)
            cg = json.dumps(g["pre_actions"][0].get("function", {}).get("parameters"), sort_keys=True)
            if ca != cg:
                diffs.append(f"{name}.pre_actions[0]: parameters changed on save")
    if len(sent.get("tools") or []) != len((got or {}).get("tools") or []):
        diffs.append(f"tools: sent {len(sent.get('tools') or [])}, stored {len((got or {}).get('tools') or [])}")
    for t in (got or {}).get("tools") or []:
        if not t.get("pre_process_code"):
            diffs.append(f"tools.{t.get('function', {}).get('name')}: pre_process_code missing after save")
    return diffs
