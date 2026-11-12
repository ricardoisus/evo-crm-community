#!/usr/bin/env python3
"""Operator-only Portainer control. No secrets in arguments, files or output."""
import json
import shlex
import subprocess
import sys

if sys.argv[1:] not in [["pilot"], ["all"], ["off"]]:
    raise SystemExit("Usage: agent-control.py pilot|all|off (all requires Ricardo's authorization)")
sys.path.insert(0, "/Users/ricardohenrique/Private")
from private_env import load_private_env

credential = load_private_env("vetorial.env", keys=["VETORIAL_PORTAINER_CREDENTIAL"])["VETORIAL_PORTAINER_CREDENTIAL"]
remote = r'''
import json,sys,urllib.request,urllib.error,yaml
p=json.load(sys.stdin)
headers={'Content-Type':'application/json'}
def req(path,method='GET',body=None):
    request=urllib.request.Request('http://127.0.0.1:9000/api'+path,
        data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit('Portainer HTTP '+str(e.code)) from None
c=p['credential']
if c.startswith('ptr_'): headers['X-API-Key']=c
else:
    username,password=c.split(':',1) if ':' in c else ('admin',c)
    headers['Authorization']='Bearer '+req('/auth','POST',{'Username':username,'Password':password})['jwt']
names=['02_evo_crm','03_evo_crm_sidekiq','10_vetorial_agente']
stacks={s['Name']:s for s in req('/stacks') if s['Name'] in names}
if set(stacks)!=set(names): raise SystemExit('Expected agent stacks missing; no changes applied')
# Preflight ALL stacks before mutation. Only the fixed Vetorial inbox is accepted.
plans=[]
for name in names:
    s=stacks[name]
    content=yaml.safe_load(req('/stacks/'+str(s['Id'])+'/file')['StackFileContent'])
    if len(content['services'])!=1: raise SystemExit('Unexpected stack layout')
    service=next(iter(content['services'].values()))
    env=service.get('environment',{})
    if isinstance(env,list): env=dict(v.split('=',1) for v in env)
    if env.get('VETORIAL_AGENT_INBOX_ID')!='702de859-a676-4af9-a2a1-806da82785d8':
        raise SystemExit('Unexpected inbox; no changes applied')
    if p['mode']=='off':
        env['VETORIAL_AGENT_SEND_ENABLED']='false'
    else:
        env['VETORIAL_AGENT_ALL_CONTACTS']='true' if p['mode']=='all' else 'false'
        env['VETORIAL_AGENT_ALLOWLIST']='+5521973801603'
        env['VETORIAL_AGENT_ANALYSIS_ALL']='false'
        env['VETORIAL_AGENT_SEND_ENABLED']='true'
    service['environment']=env
    if name=='10_vetorial_agente': service['deploy']['replicas']=0 if p['mode']=='off' else 1
    plans.append((s,content))
for s,content in plans:
    req('/stacks/'+str(s['Id'])+'?endpointId='+str(s['EndpointId']),'PUT',
        {'StackFileContent':yaml.safe_dump(content,sort_keys=False),'Env':s.get('Env',[]),'Prune':False,'PullImage':False})
    print('Updated '+s['Name']+' mode='+p['mode'])
'''
command = 'pid=$(docker inspect -f "{{.State.Pid}}" $(docker ps -q --filter name=portainer_portainer)); nsenter -t "$pid" -n python3 -c ' + shlex.quote(remote)
result = subprocess.run(["ssh", "hetzner", command], input=json.dumps({"credential": credential, "mode": sys.argv[1]}), text=True)
raise SystemExit(result.returncode)
