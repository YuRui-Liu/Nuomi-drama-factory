import json
import sqlite3
from types import SimpleNamespace

import pytest

from novelvideo.creation import agent_model
from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from .test_delegations import ctx


@pytest.fixture(autouse=True)
def no_global_provider_patches(monkeypatch):
    from novelvideo import llm_instrumentation
    monkeypatch.setattr(llm_instrumentation,'install_provider_instrumentation',lambda:None)


def test_availability_no_files_secret_hydration_or_network(ctx,tmp_path,monkeypatch):
    from novelvideo import config
    from novelvideo import text_runtime_settings as settings
    from novelvideo import model_gateway_settings as gateway
    monkeypatch.setattr(config,'STATE_DIR',tmp_path/'global')
    monkeypatch.setattr(settings,'is_self_hosted',lambda:True)
    monkeypatch.setattr(gateway,'_hydrate_secrets',lambda *_:pytest.fail('must not hydrate secrets'))
    result=agent_model.availability(ctx)
    assert result['available'] is False and result['code']=='runtime_unconfigured'
    assert not ctx.state_dir.exists() and not config.STATE_DIR.exists()


def test_readonly_metadata_freezes_route_without_exposing_endpoint(ctx,tmp_path,monkeypatch):
    from novelvideo import config
    from novelvideo import text_runtime_settings as settings
    monkeypatch.setattr(config,'STATE_DIR',tmp_path/'global')
    monkeypatch.setattr(settings,'is_self_hosted',lambda:True)
    db=config.STATE_DIR/'local/settings.db';db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE runtime_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
        conn.execute('INSERT INTO runtime_settings VALUES (?,?,?)',('text_runtime_config',json.dumps({
            'provider':'openai_compatible','base_url':'https://provider.invalid/private','model':'test-model',
            'api_key_ref':'secret-reference'}),'v1'))
        conn.execute('INSERT INTO runtime_settings VALUES (?,?,?)',('text_task_routing_v1',json.dumps({
            'routes':{'creation_agent_decision':{'runtime':'model_api','model':'test-model'}}}),'v1'))
    before=db.read_bytes()
    result=agent_model.availability(ctx)
    assert result['available'] and result['route']['model']=='test-model'
    assert 'secret-reference' not in json.dumps(result) and 'provider.invalid' not in json.dumps(result)
    assert db.read_bytes()==before
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE runtime_settings SET value=? WHERE key=?',(json.dumps({
            'routes':{'creation_agent_decision':{'runtime':'codex','model':'test-model'}}}),'text_task_routing_v1'))
    assert agent_model.availability(ctx)['code']=='runtime_unavailable'


async def test_actual_model_api_path_has_no_tools_no_retries_and_trusted_usage(ctx,monkeypatch):
    from novelvideo.text_task_runtime import runtime
    from novelvideo import config
    from novelvideo import ports
    calls=[];contexts=[]
    class Agent:
        def __init__(self,**kwargs):
            calls.append(kwargs)
        async def run(self,prompt,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output={'message':'两个方案','actions':[], 'delegations':[],
                'finished':True,'unmet':['尚未输出候选']},usage=lambda:SimpleNamespace(input_tokens=31,output_tokens=14),
                response=SimpleNamespace(provider_response_id='response-1'))
    class Meter:
        def set_llm_usage_context(self,*args,**kwargs):contexts.append((args,kwargs))
        def clear_llm_usage_context(self):contexts.append('clear')
    monkeypatch.setattr(ports,'get_usage_meter',lambda:Meter())
    monkeypatch.setattr(runtime,'Agent',Agent)
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:calls.append(k) or object())
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',runtime='model_api',model='test')
    result=await agent_model.run_decision(ctx,route,'fixed prompt',run_id='run',attempt_id='attempt')
    assert calls[0]['max_retries_override']==0
    assert calls[1]['retries']==0 and calls[1]['output_retries']==0
    assert not {'tools','toolsets','mcp_servers'} & calls[1].keys()
    assert calls[2]['usage_limits'].request_limit==1
    assert result['usage']['input_tokens']==31 and result['usage']['output_tokens']==14
    assert contexts[0][0][0]=='alice' and contexts[0][1]['project_id']=='p'
    assert contexts[-1]=='clear'


async def test_invalid_decision_never_repairs_or_falls_back(ctx,monkeypatch):
    from novelvideo.text_task_runtime import runtime
    from novelvideo import config,ports
    calls=[]
    class Agent:
        def __init__(self,**kwargs):pass
        async def run(self,*a,**k):calls.append(1);return SimpleNamespace(output={'fake':'invalid'})
    monkeypatch.setattr(runtime,'Agent',Agent)
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:object())
    monkeypatch.setattr(ports,'get_usage_meter',lambda:SimpleNamespace(set_llm_usage_context=lambda *a,**k:None,clear_llm_usage_context=lambda:None))
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',model='test')
    with pytest.raises(Exception):
        await agent_model.run_decision(ctx,route,'fixed',run_id='run',attempt_id='attempt')
    assert calls==[1]


@pytest.mark.parametrize('valid',[True,False])
async def test_real_pydantic_agent_one_model_request_without_tools(ctx,monkeypatch,valid):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,TextPart
    from pydantic_ai.usage import RequestUsage
    from novelvideo import config,ports
    requests=[]
    async def respond(messages,info):
        requests.append(info)
        payload={'message':'已准备','actions':[],'delegations':[],'finished':True,'unmet':[]} if valid else {'invalid':True}
        return ModelResponse(parts=[TextPart(json.dumps(payload))],usage=RequestUsage(input_tokens=25,output_tokens=11))
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:FunctionModel(respond))
    monkeypatch.setattr(agent_model,'availability',lambda _:None)
    monkeypatch.setattr(ports,'get_usage_meter',lambda:SimpleNamespace(set_llm_usage_context=lambda *a,**k:None,clear_llm_usage_context=lambda:None))
    # Keep the real Agent and validation loop; only its network model is replaced.
    from novelvideo import llm_instrumentation
    monkeypatch.setattr(llm_instrumentation,'install_provider_instrumentation',lambda:None)
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',model='test')
    if valid:
        assert (await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a'))['decision']['finished']
    else:
        with pytest.raises(Exception) as failure:await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a')
        assert failure.value.code=='model_response_invalid'
        assert failure.value.details['usage']['input_tokens']==25
        assert failure.value.details['usage']['output_tokens']==11
    assert len(requests)==1
    assert not requests[0].function_tools and not requests[0].output_tools


async def test_real_openai_client_retry_override_is_zero(monkeypatch):
    from novelvideo import config
    monkeypatch.setattr(config,'load_text_runtime_settings',lambda:SimpleNamespace(
        model='test',api_key='test-key-not-a-credential',base_url='https://invalid.example/v1'))
    model=config.get_newapi_text_pydantic_model('TEXT_TASK_RUNTIME_MODEL','test',
        model_name_override='selected-model',max_retries_override=0)
    assert model.model_name=='selected-model'
    assert model.client.max_retries==0
    await model.client.close()


@pytest.mark.parametrize('failure',[None,'reserve','usage','tokens','invalid','refund'])
async def test_real_instrumentation_lifecycle_is_reported(ctx,monkeypatch,failure):
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,TextPart
    from pydantic_ai.usage import RequestUsage
    from novelvideo import config,ports,llm_instrumentation as instrumentation
    monkeypatch.setattr(Agent,'run',Agent.run)
    monkeypatch.setattr(instrumentation,'_agent_run_patched',False)
    instrumentation._install_agent_run_patch()
    calls=[]
    class Meter:
        set_llm_usage_context=staticmethod(instrumentation.set_llm_usage_context)
        clear_llm_usage_context=staticmethod(instrumentation.clear_llm_usage_context)
        async def reserve_current_model_call_credit(self,**kwargs):
            calls.append('reserve')
            if failure=='reserve':raise RuntimeError('private billing detail')
            return 'reservation'
        async def refund_model_call_credit_reservation(self,identifier):
            calls.append('refund')
            if failure=='refund':raise RuntimeError('private refund detail')
        async def bump_model_call(self,**kwargs):
            calls.append('usage')
            if failure=='usage':raise RuntimeError('private usage detail')
        async def record_llm_tokens(self,**kwargs):
            calls.append('tokens')
            if failure=='tokens':raise RuntimeError('private token detail')
    async def respond(messages,info):
        calls.append('model')
        payload={'message':'候选','actions':[],'delegations':[],'finished':True,'unmet':[]}
        if failure in {'invalid','refund'}:payload={}
        return ModelResponse(parts=[TextPart(json.dumps(payload))],usage=RequestUsage(input_tokens=17,output_tokens=9))
    monkeypatch.setattr(ports,'get_usage_meter',lambda:Meter())
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:FunctionModel(respond))
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',model='test')
    if failure in {'reserve','invalid','refund'}:
        with pytest.raises(Exception) as error:
            await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a')
        usage=error.value.details['usage']
    else:
        usage=(await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a'))['usage']
    assert usage['accounting']['status']==('recorded' if failure is None else 'unknown')
    assert 'private' not in json.dumps(usage)
    assert calls.count('model')==(0 if failure=='reserve' else 1)
    if failure in {'invalid','refund'}:
        assert 'refund' in calls and usage['input_tokens']==17
    if failure in {'reserve','usage','tokens','refund'}:
        assert {'stage':failure,'status':'failed'} in usage['accounting']['events']
