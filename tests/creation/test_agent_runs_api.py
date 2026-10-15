import pytest

from novelvideo.creation import agent_model
from .test_delegations import ctx,ready,service
from .test_delegation_api import api
from .test_agent_tools import task,action
from .test_agent_runs import runs,start_request,decision


async def test_actual_routes_start_history_replay_and_owner(api,ready,task,runs,monkeypatch):
    client,user=api
    calls=[]
    async def model(*a,**k):calls.append(1);return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    query={'canvas_id':ready[0],'conversation_id':ready[1]}
    base='/projects/p/creation'
    body=start_request(task);body.pop('delegation_id')
    url=base+'/delegations/'+task['delegation_id']+'/runs'
    response=await client.post(url,params=query,json=body)
    assert response.status_code==200,response.text
    receipt=response.json()['data'];identifier=receipt['run']['id']
    current=(await client.get(base+'/agent-runs/'+identifier,params=query)).json()['data']
    assert current['state']=='delivered' and len(current['outputs'])==1
    assert (await client.post(url,params=query,json=body)).json()['data']==receipt
    assert calls==[1]
    assert (await client.get(base+'/agent-run-requests/start',params=query)).json()['data']==receipt
    assert len((await client.get(url,params=query)).json()['data'])==1
    user.clear();user.update(id='bob',username='bob')
    assert (await client.get(base+'/agent-runs/'+identifier,params=query)).status_code==403
    assert (await client.get(base+'/agent-run-requests/start',params=query)).status_code==404


async def test_runtime_and_unknown_reads_do_not_initialize(api,ctx,monkeypatch):
    client,user=api
    monkeypatch.setattr(agent_model,'availability',lambda _: {'available':False,'code':'runtime_unconfigured'})
    assert (await client.get('/projects/p/creation/agent-runtime')).json()['data']['available'] is False
    q={'canvas_id':'missing','conversation_id':'missing'}
    assert (await client.get('/projects/p/creation/agent-runs/missing',params=q)).status_code in (403,404)
    assert not ctx.state_dir.exists()
    user['legacy']=True
    assert (await client.get('/projects/p/creation/agent-runtime')).status_code==409
