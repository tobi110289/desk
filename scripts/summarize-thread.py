import sys,json,re
raw=open(sys.argv[1]).read()
try:
    d=json.loads(raw)
except Exception:
    j=json.loads(raw); d=json.loads(j[0]['text'])
if isinstance(d,list): d=json.loads(d[0]['text'])
print('status',d['status'],'| customer',d['customer'].get('fullName'),d['customer'].get('email'),'| fields',{k:v for k,v in d['customFields'].items() if k in('tenant','affectedStage','githubPr','agentReadiness','impactLevel')})
for e in d['timeline']:
    et=e['entryType']
    if et in('UNKNOWN','DISCUSSION'): continue
    who=(e.get('createdBy') or {}).get('name') or ''
    body=e.get('textContent') or e.get('text') or e.get('markdown') or e.get('content') or e.get('title') or ''
    body=re.sub(r'\s+',' ',str(body))
    n=int(sys.argv[2]) if len(sys.argv)>2 else 700
    if et in('EMAIL','CUSTOM','CHAT','SLACK'): n=max(n,1400)
    print(e['timestamp'],et,who,'|',body[:n]); print()
