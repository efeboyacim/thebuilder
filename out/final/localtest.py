import json, sys, importlib.util
ARGV = list(sys.argv)
sys.stdout.reconfigure(encoding='utf-8')
spec = importlib.util.spec_from_file_location('g', 'gen_workflow.py')
sys.argv = ['x', 'workflow.json']
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
D = json.load(open('../../hidden/test-environment/test-customers.json', encoding='utf-8'))
def flat(prefix, obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items(): flat(prefix + '.' + k, v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj): flat(prefix + '.' + str(i), v, out)
    else:
        out[prefix] = ('true' if obj is True else 'false' if obj is False else obj)
def run(code, vars):
    exec(code, {'vars': vars})
now = ARGV[1] if len(ARGV) > 1 else '2026-10-12T10:30:00+03:00'
only = []
for c in D['customers']:
    cn = c['customerNo']
    if only and cn not in only: continue
    sv = c['services']; V = {'customerNumber': cn, 'now': now}
    r = sv['cards.list']; V['load_cards_status_code'] = str(r['http'])
    if r['http'] != 200: V['load_cards_failure_count'] = 1
    else:
        tmp = {}; flat('load_cards_response.data', r['response'], tmp)
        for k, v in tmp.items(): V[k] = str(v).replace('*', '')  # platform strips asterisks
    r = sv.get('contact.email.get'); V['load_contact_status_code'] = str(r['http'])
    if r['http'] != 200: V['load_contact_failure_count'] = 1
    else:
        tmp = {}; flat('load_contact_response.data', r['response'], tmp); V.update(tmp)
    run(g.PREP_CARDS, V)
    print('==', cn, V['cards_state'], '|', V.get('select_question'), '|', V.get('email_state'), V.get('email_q'))
    if V['cards_state'] in ('error', 'none'): continue
    cards = json.loads(V['cards_json'])
    for card in cards:
        if not card['main']: continue
        W = dict(V)
        bal = sv['cards.balance'].get(card['num'])
        if not bal: print('  no balance for', card['num']); continue
        W['balance_src'] = 'tool'; tmp = {}; flat('pick_card_response.data', bal['response'], tmp)
        for k, v in tmp.items(): W[k] = str(v).replace('*', '')
        run(g.BUILD_READING, W)
        print('  ', card['num'], W['reading_state'], W.get('reading_error', ''), '| fu=', W['followup_kind'])
        print('     ', W['hub_intro'])
        wk = sv['statement.week.get'].get(card['num'])
        if wk and wk['http'] == 200:
            tmp = {}; flat('week_load_response.data', wk['response'], tmp)
            W.update(tmp); W['week_load_status_code'] = '200'; W['wanted_week'] = '3'
            run(g.WEEK_PREP, W); print('      WEEK', W['week_state'], W.get('week_ask_text'), '| warn:', W.get('week_warn'))
            ch = W.get('chosen_week')
            if W['week_state'] == 'direct':
                dc = sv['statement.week.decide'][card['num']][ch]
                tmp = {}; flat('week_decide_response.data', dc['response'], tmp); W.update(tmp); W['week_decide_status_code'] = str(dc['http'])
                run(g.DECIDE_CHECK, W); print('      CONFIRM', W['decide_state'], W.get('confirm_text'))
                if W['decide_state'] == 'ok':
                    cc = sv['statement.week.change'][card['num']][ch]
                    tmp = {}; flat('week_change_response.data', cc['response'], tmp); W.update(tmp); W['week_change_status_code'] = str(cc['http'])
                    run(g.CHANGE_CHECK, W); print('      DONE', W['change_state'], W.get('hub_intro'))
