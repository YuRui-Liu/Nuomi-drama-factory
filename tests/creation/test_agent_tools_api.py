import pytest

from .test_delegations import ctx, ready, service
from .test_delegation_api import api
from .test_agent_tools import task, action


async def test_real_actions_receipt_outputs_and_owner_isolation(api,ready,task):
    client,user=api
    base='/projects/p/creation/delegations/'+task['delegation_id']
    query={'canvas_id':ready[0],'conversation_id':ready[1]}
    assert (await client.get(base+'/outputs',params=query)).json()['data']==[]
    response=await client.post(base+'/actions',params=query,json=action())
    assert response.status_code==200,response.text
    result=response.json()['data']
    assert result['output']['state']=='candidate'
    assert (await client.post(base+'/actions',params=query,json=action())).json()['data']==result
    assert (await client.get(base+'/action-requests/write',params=query)).json()['data']==result
    assert len((await client.get(base+'/outputs',params=query)).json()['data'])==1
    for identity in ({'id':'viewer','username':'alice'}, {'id':'bob','username':'bob'},
                     {'id':'alice','username':'alice','credential_kind':'agent_session'}):
        user.clear();user.update(identity)
        assert (await client.post(base+'/actions',params=query,json=action(request_id='forbidden'))).status_code==403
    user.clear();user.update(id='bob',username='bob')
    assert (await client.get(base+'/outputs',params=query)).status_code==403
    assert (await client.get(base+'/action-requests/write',params=query)).status_code==403
    user.clear();user.update(id='alice',username='alice')
    assert (await client.get(base+'/action-requests/write',params={**query,'canvas_id':'other'})).status_code==403


async def test_unknown_queries_do_not_initialize_and_legacy_guard(api,ctx):
    client,user=api
    base='/projects/p/creation/delegations/missing'
    query={'canvas_id':'unknown','conversation_id':'unknown'}
    for path in ('/outputs','/action-requests/missing'):
        assert (await client.get(base+path,params=query)).status_code in (403,404)
    assert not ctx.state_dir.exists()
    user['legacy']=True
    assert (await client.get(base+'/outputs',params=query)).status_code==409
    assert not ctx.state_dir.exists()


@pytest.mark.parametrize('payload',[{}, {'request_id':'x','operation':{},'payload':{}},
                                  {'request_id':'x','operation':'context.read','payload':[]},
                                  {'request_id':'x','operation':'http','payload':{}}])
async def test_malformed_or_generic_actions_are_visible_4xx(api,ready,task,payload):
    client,_=api
    base='/projects/p/creation/delegations/'+task['delegation_id']
    response=await client.post(base+'/actions',params={'canvas_id':ready[0],'conversation_id':ready[1]},json=payload)
    expected=409 if payload.get('operation')=='http' else 422
    assert response.status_code==expected,response.text
    if expected==409:
        assert response.json()['detail']['code']=='capability_denied'
