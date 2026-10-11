"""Print a call's turns and engine events compactly: python tools/call_events.py <call_id> [first_message_index]
(customer/agent turns, extracted variables, routes, tool calls). Needs env.sh sourced."""
import json, sys
sys.path.insert(0, '.')
import freya

msgs = freya.get_call(sys.argv[1])['messages']
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
for i, x in enumerate(msgs[start:], start):
    if x.get('role') in ('user', 'assistant'):
        print(i, x['role'][:4], '|', (x.get('message') or '')[:120])
        continue
    try:
        e = json.loads(x.get('message') or '{}')
    except ValueError:
        print(i, 'sys raw', str(x.get('message'))[:150]); continue
    ev = e.get('event')
    if ev == 'function_call_start':
        print(i, ev, e.get('function'), json.dumps(e.get('args'))[:80] if e.get('function') != 'code_exec' else '')
    elif ev == 'function_call_complete':
        r = e.get('result') or {}
        print(i, ev, e.get('function'), r.get('status'), r.get('status_code'), str(r.get('message'))[:120], str(r.get('error'))[:200])
    elif ev == 'variable_extracted':
        print(i, 'x', e.get('name'), '=', str(e.get('value'))[:60])
    elif ev == 'route_evaluated':
        print(i, 'route', e.get('from_node'), '->', e.get('target'), str(e.get('reason'))[:80])
    elif ev == 'node_enter':
        print(i, 'enter', e.get('node'))
    else:
        print(i, ev, json.dumps(e)[:150])
