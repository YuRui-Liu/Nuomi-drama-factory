import pytest


def test_instrumental_shared_duration_and_big_seed():
    from novelvideo.media_capabilities.music.runninghub_acestep import MusicRequest, compile_request
    req = MusicRequest(tags='piano', durationSeconds=15, seed='18446744073709551615')
    fields = {(x['nodeId'], x['fieldName']): x['fieldValue'] for x in compile_request(req)}
    assert fields['94', 'lyrics'] == '[Instrumental]'
    assert fields['205', 'value'] == '15'
    assert fields['109', 'value'] == '18446744073709551615'
    assert ('98', 'seconds') not in fields


@pytest.mark.parametrize('values', [{'seed':'-1'}, {'durationSeconds':float('nan')}, {'tags':' '}, {'durationSeconds':0}])
def test_invalid_parameters_rejected(values):
    from novelvideo.media_capabilities.music.runninghub_acestep import MusicRequest
    with pytest.raises(ValueError): MusicRequest(**{'tags':'piano', **values})


def test_supplied_graph_contract():
    import json
    from pathlib import Path
    from novelvideo.media_capabilities.music.runninghub_acestep import validate_contract
    graph = json.loads((Path(__file__).parents[2] / 'runninghub/Ace-Step1.5X工作流(打造你的专属音乐)_api.json').read_text())
    validate_contract(graph)
    graph['98']['inputs']['seconds'] = ['203', 0]
    with pytest.raises(ValueError): validate_contract(graph)


@pytest.mark.asyncio
async def test_read_only_workflow_fetch():
    import json
    import httpx
    from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient
    def handle(request):
        assert request.url.path == '/api/openapi/getJsonApiFormat'
        assert json.loads(request.content)['workflowId'] == '123'
        return httpx.Response(200, json={'code':0,'data':{'prompt':json.dumps({'94':{'class_type':'test'}})}})
    async with RunningHubClient('test-key', transport=httpx.MockTransport(handle)) as client:
        assert (await client.workflow_json('123'))['94']['class_type'] == 'test'
