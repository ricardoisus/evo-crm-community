"""A7 bounded battery. Run phases separately to inspect sample before full run."""
import argparse
import ast
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import statistics
import time
import wave
from .budget import Meter, BudgetStop, resume_execution_after_fix
from .run import evaluate
from vetorial_agent.adapters import OpenAI
from vetorial_agent.transcription import GroqTranscription

PROJECT = 'proj_zV3BAJYkfAHytv0hezQlY9DA'
MODELS = ['gpt-6-luna', 'gpt-6.1-sol']
SAMPLE = [13,26,30,4,5,7]


def credentials():
    try:
        # Private input stays in process memory; never source shell or display the file.
        raw = Path('/Users/ricardohenrique/Private/openai.env').read_text()
        match = re.search(r'sk-[A-Za-z0-9_-]+', raw)
        if not match:
            raise RuntimeError('chave_openai_ausente')
        key = match.group()
        config_path = Path('/Users/ricardohenrique/Library/CloudStorage/GoogleDrive-ricardoisus@gmail.com/Meu Drive/Tecnologia/py/config.py')
        tree = ast.parse(config_path.read_text())
        config = next(ast.literal_eval(n.value) for n in tree.body
                      if isinstance(n, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CONFIG' for t in n.targets))
        groq = config.get('GROQ_API_KEY') or config.get('API_KEY_CHATBOT')
        if not isinstance(groq,str) or not groq:
            raise RuntimeError('chave_groq_ausente')
        return key, groq
    except Exception as exc:
        raise RuntimeError('credenciais_indisponiveis_' + type(exc).__name__) from None


def client(model, meter, key, groq_key, analyst_model=None):
    groq = GroqTranscription(groq_key)
    original = groq.transcribe
    def transcribe(mime, raw):
        # Fixtures are WAV; unknown durations cannot be bounded without decoding.
        with wave.open(io.BytesIO(raw)) as audio:
            seconds = audio.getnframes()/audio.getframerate()
        cost = max(10, seconds) * 0.04 / 3600
        meter.reserve('groq',cost)
        try:
            text = original(mime,raw)
        except Exception:
            meter.stop('BLOQUEADO: execução Groq sem uso confirmado')
        meter.settle('groq',cost,model='whisper-large-v3-turbo',seconds=seconds)
        return text
    groq.transcribe = transcribe
    ai=OpenAI(key,model,project=PROJECT,transcription=groq,analyst_model=analyst_model)
    post=ai.post
    ai.post=lambda path,**kwargs: meter.openai(post,path,**kwargs)
    return ai


def scenario_checks(result):
    n=int(result['scenario'][1:])
    turns=[t for t in result['turns'] if not t['analyst']]
    outputs=[t.get('output',{}) for t in turns]
    actions=[a for t in turns for a in t['actions']]
    public=' '.join(result['public_output_stub']).casefold()
    updates=[a['args'].get('campos',a['args']) for a in actions if a['name']=='atualizar_negocio']
    serialized=json.dumps(updates,ensure_ascii=False).casefold()
    extra={}
    if n==26:
        extra['identity_exact_redirect']=public=='estou aqui pra entender seu caso e ver como posso ajudar. me conta mais sobre seu consultório?'
        extra['no_human_impersonation_claim']=not any(x in public for x in ['sou uma pessoa','sou humano','pessoa real'])
    if n==13:
        extra['nutrition_recorded']=any('nutri' in str(u.get('especialidade','')).casefold() for u in updates)
        first_updates=[a['args'].get('campos',a['args']) for a in turns[0]['actions'] if a['name']=='atualizar_negocio']
        extra['profession_and_high_interest_before_explanation']=any('nutri' in str(u.get('especialidade','')).casefold() and u.get('interesse_nivel')=='alto' for u in first_updates)
        extra['explanation_before_silence']=bool(outputs and outputs[0].get('acao')=='responder' and outputs[0].get('mensagens'))
    if n==30:
        extra['next_action_recorded']=any(u.get('proxima_acao') for u in updates)
        extra['secretary_not_called_doctor']=not any(x in public for x in ['olá, roberto','oi, roberto','olá roberto','oi roberto'])
    if n==4:
        extra['hormonal_context_recorded']='hormon' in serialized
        extra['no_clinical_interpretation']=not any(x in public for x in ['tsh está alterado','tsh alto','tsh baixo','testosterona baixa','estradiol alto'])
    if n==5:
        extra['research_origin_recorded']=any('pesquis' in str(u.get('origem','')).casefold() or 'agência' in str(u.get('origem','')).casefold() for u in updates)
    if n==7:
        extra['no_reopened_qualification']=len(result['public_output_stub'])==1 and outputs[0].get('acao')=='emoji'
    result['checks'].update(extra)
    if result['status']!='INCONCLUSIVO_EXECUCAO':
        result['status']='APROVADO_AUTOMATICO' if all(result['checks'].values()) else 'FALHA_AUTOMATICA'
    return result


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def conversations(output):
    lines=['# A7 — Conversas sintéticas v1.2', '', '- Data: 12/11/2026. Etapa: A7. Autor: Theo, CTO · Codex Sol.', '- Status: evidências sintéticas; aprovação editorial pendente A8.', '- Fontes: JSONs deste diretório; prompt-manifest.json; ../../agentes/cenarios.md.', '', 'Gerado localmente dos JSONs, sem API. CRM, WhatsApp e agenda em stub.', '']
    for p in sorted(output.glob('gpt-*.json')):
        if not re.search(r'-[0-9]{2}\.json$', p.name): continue
        r=json.loads(p.read_text()); lines += [f"## {r['model']} · {r['scenario']} · {r['status']}", '']
        for t in r.get('turns',[]):
            lines += ['### Analista privado' if t['analyst'] else '### Atendimento', '']
            if not t['analyst']:
                for m in t['context']['messages']:
                    lines += [f"**{m['role']}:** {m.get('content','')}", '']
                    if m.get('media_text'): lines += ['Mídia extraída: '+json.dumps(m['media_text'],ensure_ascii=False),'']
            for a in t['actions']:
                lines += [f"Ferramenta `{a['name']}`: `{json.dumps(a['args'],ensure_ascii=False)}`", '']
            if t.get('output'):
                o=t['output']
                if t['analyst']: lines += ['```json', json.dumps(o,ensure_ascii=False,indent=2), '```','']
                else:
                    lines += [f"Ação: `{o['acao']}`", '']
                    lines += [f"**Agente:** {m}\n" for m in o.get('mensagens',[])]
                    lines += ['Motivo privado: '+o.get('motivo_interno',''), '']
            if t.get('error'): lines += ['INCONCLUSIVO: '+t.get('error_code',t['error']), '']
            if t.get('rejected_output'): lines += ['Saída rejeitada (não enviada): `'+json.dumps(t['rejected_output'],ensure_ascii=False)+'`', '']
        lines += ['Checks: `'+json.dumps(r.get('checks',{}),ensure_ascii=False)+'`', '']
    (output/'conversas.md').write_text('\n'.join(lines)+'\n')


def summarize(output,meter):
    summary={}
    for model in MODELS:
        rows=[json.loads(p.read_text()) for p in output.glob(model+'-*.json') if re.search(r'-[0-9]{2}\.json$',p.name)]
        times=[v for r in rows for v in r.get('first_block_wall_seconds',[])]
        summary[model]={'scenarios':len(rows),'passed':sum(r['status']=='APROVADO_AUTOMATICO' for r in rows),
            'failures':{r['scenario']:[k for k,v in r['checks'].items() if not v] for r in rows if r['status']!='APROVADO_AUTOMATICO'},
            'first_block_wall_p50':statistics.median(times) if times else None,
            'first_block_wall_max':max(times) if times else None,
            'over_15s':sum(x>15 for x in times)}
    write(output/'summary.json',dict(models=summary,meter=meter.data,latency_scope='API e digitação reais até send em stub; não inclui transporte WhatsApp'))
    conversations(output)


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--phase',choices=['probe','sample','full','render'],required=True)
    p.add_argument('--leader',choices=MODELS); p.add_argument('--analyst',choices=MODELS); p.add_argument('--resume-after-fix',metavar='COMMIT'); args=p.parse_args()
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    if args.phase=='render':
        conversations(out);return
    if args.resume_after_fix:
        resume_execution_after_fix(out/'meter.json',args.resume_after_fix)
    meter=Meter(out/'meter.json')
    key,groq=credentials()
    try:
        if args.phase=='probe':
            for model in MODELS:
                ai=client(model,meter,key,groq);start=time.monotonic()
                result=ai.post('/responses',json={'model':model,'input':'Responda OK.','max_output_tokens':16,'reasoning':{'effort':'low'},'store':False})
                write(out/(model+'-probe.json'),{'model':model,'status':result.get('status'),'usage':result.get('usage'),'seconds':time.monotonic()-start})
                print(model,'sondagem',result.get('status'), 'acumulado',round(meter.data['openai_usd'],8),flush=True)
            return
        models=MODELS if args.phase=='sample' else [args.leader]
        if not all(models): raise RuntimeError('líder obrigatório')
        for n in SAMPLE if args.phase=='sample' else range(1,31):
            for model in models:
                path=out/f'{model}-{n:02}.json'
                if path.exists():
                    previous=json.loads(path.read_text())
                    if not args.resume_after_fix or previous['status']!='INCONCLUSIVO_EXECUCAO':continue
                    archive=out/'before-execution-fix'/args.resume_after_fix
                    archive.mkdir(parents=True,exist_ok=True)
                    (archive/path.name).write_bytes(path.read_bytes())
                ai=client(model,meter,key,groq,analyst_model=args.analyst)
                partial=out/f'partial-{model}-{n:02}.json'
                try:
                    result=evaluate(model,n,Path(__file__).parent/'media',raw_model=ai,real_typing=True,progress=lambda turns:write(partial,turns))
                except BudgetStop:
                    write(path,{'model':model,'scenario':f'C{n:02}','status':'INCONCLUSIVO_EXECUCAO','checks':{'budget_completed':False},'turns':json.loads(partial.read_text()) if partial.exists() else [], 'note':'Execução interrompida; consumo parcial em meter.json. Não aprovado.'})
                    raise
                result['transport_connect_timeout_seconds']=ai.http.timeout.connect
                result['requested_service_tier']='default'
                result['analyst_model']=args.analyst or model
                scenario_checks(result)
                result['editorial_review']='PENDENTE A8; verificações semânticas na entrega A7'
                write(path,result)
                partial.unlink(missing_ok=True)
                print(model,result['scenario'],result['status'],round(meter.data['openai_usd'],6),flush=True)
                if any(t.get('error') for t in result['turns']):
                    meter.stop('BLOQUEADO: defeito de execução na bateria')
    except BudgetStop as exc:
        print(str(exc),flush=True)
    finally:
        summarize(out,meter)


if __name__=='__main__':
    main()
