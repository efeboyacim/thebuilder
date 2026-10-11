import json,sys
sys.stdout.reconfigure(encoding='utf-8')
d=json.load(open(sys.argv[1],encoding='utf-8'))
for c in d['customers']:
    s=c['services']; print('==',c['customerNo'])
    for svc,v in s.items():
        if 'http' in v:
            r=v['response']; extra=' delay=%s'%v.get('delaySeconds') if 'delaySeconds' in v else ''
            if v['http']!=200: print(' ',svc,v['http'],r['result']['code'],extra)
            elif svc=='contact.email.get': print(' ',svc,r['data']['communicationType'],repr(r['data']['ibanEmail']),extra)
            elif svc=='cards.list': print(' ',svc,[(x['cardNumber'],x['productName'],x['isMainCard']) for x in r['data']['cards']],extra)
            else: print(' ',svc,v['http'],json.dumps(r.get('data'),ensure_ascii=False),extra)
        else:
            for card,w in v.items():
                if 'http' in w:
                    r=w['response']; extra=' delay=%s'%w.get('delaySeconds') if 'delaySeconds' in w else ''
                    if w['http']!=200: print(' ',svc,card,w['http'],r['result']['code'],extra); continue
                    dd=r['data']
                    if svc=='cards.balance':
                        blocks={k:(b['statementPaymentStatus'],b['statementDebt'],b['minPayment'],b['remainingDebt'],b['remainingMinPayment'],b['totalDebt'],b['transferAmount'],b['remainAutomaticPaymentAmount']) for k,b in dd.items() if k.startswith('debt')}
                        print(' ',svc,card,dd['productName'],dd['statementDate'],dd['lastPayDate'],dd['nextStatementDate'],dd['nextLastPayDate'],dd['statementPaymentStatus'],dd['automaticPaymentFlag'],extra); 
                        for k,b in blocks.items(): print('     ',k,b)
                    elif svc=='statement.week.get':
                        print(' ',svc,card,dd['statementWeek'],dd['statementDay'],dd['nextStatementDate'],dd['nextLastPayDate'],repr(dd['relationType']),dd['relatedCardProductName'],extra)
                else:
                    for wk,x in w.items():
                        extra=' delay=%s'%x.get('delaySeconds') if 'delaySeconds' in x else ''
                        r=x['response']
                        if x['http']!=200: print(' ',svc,card,wk,x['http'],r['result']['code'],extra)
                        elif svc=='statement.week.decide': print(' ',svc,card,wk,r['data']['statementDate'],r['data']['statementDueDate'],extra)
                        else: print(' ',svc,card,wk,r['data'].get('exitState'),extra)
