import base64
import httpx
import pytest
from evals.budget import Meter, BudgetStop
from vetorial_agent.adapters import OpenAI
from vetorial_agent.transcription import GroqTranscription


def test_budget_stops_before_network_and_remains_stopped(tmp_path):
    meter = Meter(tmp_path/'meter.json', limit=0.001)
    calls = []
    with pytest.raises(BudgetStop, match='teto de gasto'):
        meter.openai(lambda *a, **k: calls.append(1), '/responses', json={
            'model':'gpt-6.1-sol', 'max_output_tokens':2500})
    assert not calls
    with pytest.raises(BudgetStop):
        Meter(tmp_path/'meter.json')


def test_usage_and_cache_accounted_even_incomplete(tmp_path):
    meter = Meter(tmp_path/'meter.json')
    result = meter.openai(lambda *a, **k: {'status':'incomplete', 'usage':{
        'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':500}}},
        '/responses', json={'model':'gpt-6.1-sol','max_output_tokens':100})
    assert result['status']=='incomplete'
    assert meter.data['openai_usd']==pytest.approx(0.00205)
    assert not meter.data['pending']


def test_quota_is_terminal(tmp_path):
    meter = Meter(tmp_path/'meter.json')
    def rejected(*a, **kw):
        raise RuntimeError('openai_status_429_insufficient_quota')
    with pytest.raises(BudgetStop, match='crédito OpenAI'):
        meter.openai(rejected, '/responses', json={'model':'gpt-6-luna','max_output_tokens':16})
    assert meter.data['stopped']=='BLOQUEADO: crédito OpenAI'


def test_uncertain_timeout_prevents_retry_after_restart(tmp_path):
    meter = Meter(tmp_path/'meter.json')
    def timeout(*a, **kw):
        raise httpx.ReadTimeout('sensitive')
    with pytest.raises(BudgetStop):
        meter.openai(timeout, '/responses', json={'model':'gpt-6-luna','max_output_tokens':16})
    assert meter.data['pending']
    with pytest.raises(BudgetStop):
        Meter(tmp_path/'meter.json')


def test_audio_uses_only_groq_pt():
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'text':'Sou cardiologista em São Paulo','duration':30})
    groq=GroqTranscription('synthetic')
    groq.http=httpx.Client(base_url='https://api.groq.com/openai/v1',transport=httpx.MockTransport(respond))
    ai=OpenAI('synthetic','gpt-6-luna',project='proj_synthetic',transcription=groq)
    ai.http=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('OpenAI chamada para áudio')))
    assert ai.media('audio/wav',base64.b64encode(b'synthetic').decode())=='Sou cardiologista em São Paulo'
    request=seen[0]
    assert request.url.host=='api.groq.com'
    assert b'whisper-large-v3-turbo' in request.content
    assert b'name="language"\r\n\r\npt' in request.content
    assert groq.usage[0]['duration']==30


def test_audio_fail_closed_without_groq():
    ai=OpenAI('synthetic','gpt-6-luna',project='proj_synthetic')
    with pytest.raises(RuntimeError,match='groq_nao_configurada'):
        ai.media('audio/wav',base64.b64encode(b'synthetic').decode())


def test_measured_latency_observes_send_after_real_typing(tmp_path, monkeypatch):
    from evals.run import evaluate
    class Model:
        usage=[]
        def run(self,context,act,analyst=False):
            if analyst:
                return {'resumo':'teste','campos':{},'etiquetas':[], 'etapa_sugerida':None,'proxima_acao':'aguardar'}
            return {'acao':'emoji','mensagens':['😊'],'motivo_interno':'cortesia'}
    sleeps=[]
    monkeypatch.setattr('evals.run.time.sleep',lambda seconds:sleeps.append(seconds))
    result=evaluate('gpt-6-luna',7,tmp_path,raw_model=Model(),real_typing=True)
    assert sleeps
    assert result['first_block_wall_seconds']
    assert result['public_output_stub']==['😊']


def test_terminal_budget_interrupt_is_not_swallowed_by_worker(tmp_path):
    from evals.run import evaluate
    class Model:
        usage=[]
        def run(self,*a,**kw):
            raise BudgetStop('BLOQUEADO: teto de gasto')
    with pytest.raises(BudgetStop,match='teto de gasto'):
        evaluate('gpt-6-luna',7,tmp_path,raw_model=Model())


def test_scenario_requires_fields_not_only_tool_name():
    from evals.a7 import scenario_checks
    row={'scenario':'C30','status':'APROVADO_AUTOMATICO','checks':{'expected_tools':True},
         'turns':[{'analyst':False,'output':{},'actions':[{'name':'atualizar_negocio','args':{'campos':{}}}]}],
         'public_output_stub':['Oi, Fernanda. Qual a especialidade do Dr. Roberto?']}
    scenario_checks(row)
    assert row['status']=='FALHA_AUTOMATICA'
    assert not row['checks']['next_action_recorded']


def test_unapproved_model_is_refused_before_spend(tmp_path):
    meter=Meter(tmp_path/'meter.json')
    calls=[]
    with pytest.raises(KeyError):
        meter.openai(lambda *a,**kw:calls.append(1),'/responses',json={'model':'gpt-6-astra','max_output_tokens':16})
    assert not calls
    assert meter.data['openai_usd']==0


@pytest.mark.parametrize('ttl,expected', [('30m',0.0025),('12h',0.005)])
def test_cache_write_uses_requested_or_default_official_rate(tmp_path,ttl,expected):
    meter=Meter(tmp_path/'meter.json')
    meter.openai(lambda *a,**kw:{'usage':{'input_tokens':1000,'output_tokens':0,
        'input_tokens_details':{'cache_write_tokens':1000,'cached_tokens':0}}}, '/responses',
        json={'model':'gpt-6.1-sol','max_output_tokens':16,'prompt_cache_options':{'ttl':ttl}})
    assert meter.data['openai_usd']==pytest.approx(expected)


def test_analyst_can_use_luna_while_conversation_uses_sol():
    ai=OpenAI('synthetic','gpt-6.1-sol',project='proj_synthetic',analyst_model='gpt-6-luna')
    payloads=[]
    def post(path,**kwargs):
        payloads.append(kwargs['json'])
        raise RuntimeError('synthetic_stop')
    ai.post=post
    with pytest.raises(RuntimeError,match='synthetic_stop'):
        ai.run({'messages':[]},lambda n,a:{},analyst=True)
    assert payloads[0]['model']=='gpt-6-luna'
    assert payloads[0]['tools']==[]
    with pytest.raises(RuntimeError,match='synthetic_stop'):
        ai.run({'messages':[]},lambda n,a:{})
    assert payloads[1]['model']=='gpt-6.1-sol'
    assert payloads[1]['tools']


def test_responses_forces_standard_tier_for_declared_budget():
    seen=[]
    def response(request):
        import json
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={'usage':{'input_tokens':1,'output_tokens':1},'service_tier':'default'})
    ai=OpenAI('synthetic','gpt-6-luna',project='proj_synthetic')
    ai.http=httpx.Client(base_url='https://api.openai.com/v1',transport=httpx.MockTransport(response))
    ai.post('/responses',json={'model':'gpt-6-luna'})
    assert seen[0]['service_tier']=='default'
    assert ai.usage[0]['_service_tier']=='default'


def test_readable_conversations_are_generated_without_credentials_or_api(tmp_path,monkeypatch):
    import json
    from evals.a7 import main
    (tmp_path/'gpt-6-luna-01.json').write_text(json.dumps({
        'model':'gpt-6-luna','scenario':'C01','status':'INCONCLUSIVO_EXECUCAO','checks':{},'turns':[]}))
    monkeypatch.setattr('evals.a7.credentials',lambda:pytest.fail('leitura indevida de credenciais'))
    monkeypatch.setattr('sys.argv',['evals.a7','--output',str(tmp_path),'--phase','render'])
    main()
    assert 'INCONCLUSIVO_EXECUCAO' in (tmp_path/'conversas.md').read_text()
