"""Local replay: act-scripts through the brain code string and the stub bank, emulating the Freya runtime.

    r = Replay(spec, api, stub, call_values)
    r.run(acts)          # -> r.transcript, r.stub.ledger, r.vars

An act is one customer turn as the talk node's extraction would give it: {"stage": ..., "main": ..., "said": ...,
other slots}. Slots the act leaves out arrive as "none". When the acts run out while the brain waits for the customer,
the customer hangs up. The emulation follows docs/spikes.md: an api node bakes the brain's a_<role>__<field>
variables; a 2xx raises <node>_success_count, writes the flattened <node>_response.data.* keys (booleans as "true")
and the mapped r_<role>__* variables (booleans as "True"); any other status raises <node>_failure_count and leaves
mapped variables untouched; the lifecycle close sends final_outcome/final_reason, without a reason when it is "-".
"""
import json

from builder import bindings as bd
from builder import brain, contract, engine
from builder import policy_lib as pl


class ReplayError(Exception):
    pass


def _flatten(prefix, obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(f"{prefix}.{k}", v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(f"{prefix}.{i}", v, out)
    elif obj is not None:
        out[prefix] = ("true" if obj else "false") if isinstance(obj, bool) else obj
    return out


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


class Replay:
    def __init__(self, spec, api, stub, call_values, code=None, max_hops=12):
        """code: the brain code string (default: built from spec and api), or a callable(vars) such as the imported
        engine."""
        self.spec, self.api, self.stub = spec, api, stub
        self.b = bd.Bindings.from_dict(spec["bindings"])
        self.code = code or brain.source(spec, api)
        self.call_values = dict(call_values)
        self.vars = dict(call_values)
        self.contract = contract.extraction(spec)
        self.transcript, self.max_hops = [], max_hops
        self.ended = None

    # ----- the call -----

    def run(self, acts):
        self._agent(pl.render(self.spec["opening"]["template"], self.call_values), "opening")
        acts = list(acts)
        while True:
            stage = self.waiting()
            if not acts:
                return self._close("hangup")
            act = dict(acts.pop(0))
            want = act.pop("stage", None)
            if want and want != stage:
                raise ReplayError(f"the act is for stage {want!r} but the brain waits in {stage!r}: {act}")
            self._turn(stage, act)
            route = self._brain()
            hops = 0
            while route.startswith(engine.API):
                self._api(route[len(engine.API):])
                route = self._brain()
                hops += 1
                if hops > self.max_hops:
                    raise ReplayError("too many api hops in one turn")
            if route == engine.END:
                self._agent(self.vars.get("farewell", ""), "end")
                return self._close("end")
            if route.startswith(engine.TALK):
                self._agent(self.vars.get("say_next", ""), route)
                continue
            if route.startswith(engine.FREE):
                self._agent(" ".join(x for x in (self.vars.get("say_next"), "[free mode: " + str(
                    self.vars.get("free_rule")) + "]", self.vars.get("free_feedback")) if x), route)
                continue
            raise ReplayError(f"route {route!r}; brain_error {self.vars.get('brain_error')!r}")

    def waiting(self):
        st = json.loads(self.vars.get(engine.STATE) or "{}")
        return st.get("wait") or "open"

    def state(self):
        return json.loads(self.vars.get(engine.STATE) or "{}")

    # ----- runtime emulation -----

    def _agent(self, text, node):
        self.transcript.append({"who": "agent", "node": node, "text": text})

    def _turn(self, stage, act):
        fields = {f["name"]: f for f in self.contract[stage]}
        for slot, value in act.items():
            name = engine.x_var(slot)
            if name not in fields:
                raise ReplayError(f"stage {stage!r} extracts no slot {slot!r} ({', '.join(fields)})")
            enum = fields[name].get("enum")
            if enum and value not in enum:
                raise ReplayError(f"{slot}={value!r} is not one of {enum} in stage {stage!r}")
        for name in fields:
            self.vars[name] = act.get(name[len("x_"):], "none")
        self.transcript.append({"who": "customer", "stage": stage, "text": act.get("said", ""), "act": act})

    def _brain(self):
        if callable(self.code):
            self.code(self.vars)
        else:
            brain.run_code(self.code, self.vars)
        if self.vars.get("brain_error"):
            raise ReplayError(f"brain error: {self.vars['brain_error']}")
        return str(self.vars.get("route") or "")

    def _api(self, role):
        tool = self.b.tools[role]
        body = {}
        for path, bind in (self.b.request.get(role) or {}).items():
            if bind.get("source") == "literal":
                _set(body, path, bind.get("value"))
                continue
            v = self.vars.get(engine.arg_var(role, path))
            if v is not None and v != "":
                _set(body, path, v)
        resp = self.stub.call(tool, body)
        node = engine.api_node(role)
        self.transcript.append({"who": "tool", "tool": tool, "body": body, "status": resp.status, "response": resp.body})
        self.vars[f"{node}_status_code"] = resp.status
        if 200 <= resp.status < 300:
            self.vars[f"{node}_success_count"] = int(self.vars.get(f"{node}_success_count") or 0) + 1
            self.vars.update(_flatten(f"{node}_response.data", resp.body or {}, {}))
            for r, path in (self.b.response.get(role) or {}).items():
                if r in ("item", "products"):
                    continue
                v = _get(resp.body or {}, path)
                if v is not None:
                    self.vars[engine.resp_var(role, r)] = str(v) if isinstance(v, bool) else v
        else:
            self.vars[f"{node}_failure_count"] = int(self.vars.get(f"{node}_failure_count") or 0) + 1

    def _close(self, how):
        self.ended = how
        outcome, reason = self.vars.get("final_outcome"), self.vars.get("final_reason")
        engine_values = {"outcome": outcome or None, "reason": None if reason in (None, "", "-") else reason}
        body = bd.render_body(self.b, "close", engine_values, self.call_values)
        resp = self.stub.call(self.b.tools["close"], body)
        self.transcript.append({"who": "tool", "tool": self.b.tools["close"], "body": body, "status": resp.status,
                                "response": resp.body})
        return self

    # ----- views -----

    def agent_texts(self):
        return [t["text"] for t in self.transcript if t["who"] == "agent"]

