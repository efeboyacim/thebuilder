"""Probe workflow: checks nested data body, flattened list keys, error keys, code node, global end_call."""
import json, sys
M = "https://freya-mock.efe-byci.workers.dev/voice/v1/"

def api_fn(name, path, data_props, timeout=15000, retries=0):
    return {"type": "function", "config": {"timeoutMs": timeout, "maxRetries": retries},
            "function": {"name": name, "handler": "__function__:api_call", "description": name,
                         "parameters": {"type": "object", "properties": {
                             "url": {"type": "string", "default": M + path},
                             "method": {"type": "string", "default": "POST"},
                             "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                             "body": {"type": "object", "properties": {
                                 "data": {"type": "object", "properties": data_props},
                                 "call": {"type": "object", "properties": {"lang": {"type": "string", "default": "tr"}}}}}}}}}

CODE = r'''
try:
    keys = sorted(k for k in vars.keys() if k.startswith('load_cards'))
    vars['dbg'] = ' | '.join(k + '=' + str(vars.get(k))[:60] for k in keys)[:1500]
except Exception as e:
    vars['dbg'] = 'ERR ' + str(e)
'''
wf = {
  "initial_node": "init",
  "system_prompt": "Türkçe konuş.",
  "test_variables": {"customerNumber": "96158684", "now": "2026-10-12T10:30:00+03:00"},
  "nodes": {
    "init": {"name": "init", "node_type": "code", "passthrough": True, "passthrough_default_transition": "load_cards", "on_enter": {"type": "silent"},
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec", "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": "vars['x']='1'"}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": [{"target_node": "load_cards", "description": "always", "condition": {"operator": "always"}}]},
    "load_cards": {"name": "load_cards", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "dbg",
                   "on_enter": {"type": "silent"},
                   "pre_actions": [api_fn("cards_list", "cards.list", {"customerNo": {"type": "string", "default": "{{customerNumber}}"}, "aiFlow": {"type": "string", "default": "CardOps"}})],
                   "response_mapping": [{"path": "data.data.exitState", "variable": "exit_state"}, {"path": "data.result.code", "variable": "res_code"}],
                   "transitions": [{"target_node": "dbg", "description": "always", "condition": {"operator": "always"}}]},
    "dbg": {"name": "dbg", "node_type": "code", "passthrough": True, "passthrough_default_transition": "talk", "on_enter": {"type": "silent"},
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec", "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": CODE}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": [{"target_node": "talk", "description": "always", "condition": {"operator": "always"}}]},
    "talk": {"name": "talk", "node_type": "conversation", "on_enter": {"type": "tts", "text": "DBG exit={{exit_state}} code={{res_code}} :: {{dbg}}"},
             "task_message": "Kısa cevap ver. Araç çağırma.", "transitions": []},
    "bye": {"name": "bye", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
            "terminal": {"type": "function", "function": {"name": "agent_transfer", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "custom"}, "spoken_message": {"type": "string", "default": "Sizi müşteri temsilcimize aktarıyorum."}}}}}}
  },
  "global_nodes": {
    "g_agent": {"name": "g_agent", "node_type": "end_call", "description": "agent request",
                "condition": {"operator": "matches_intent", "description": "The caller asks to speak to a human agent / müşteri temsilcisi"},
                "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "excluded_nodes": ["bye"], "transitions": [],
                "terminal": {"type": "function", "function": {"name": "agent_transfer", "handler": "__function__:end_call", "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "custom"}, "spoken_message": {"type": "string", "default": "Sizi müşteri temsilcimize aktarıyorum."}}}}}}
  }
}
json.dump(wf, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
