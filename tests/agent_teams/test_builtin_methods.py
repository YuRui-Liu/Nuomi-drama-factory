from hashlib import sha256

from novelvideo.agent_teams.builtin_methods import builtin_methods


def test_actual_sources_and_protocol_are_read_only():
    from novelvideo.script_creation.prompts import CRAFT_BY_KIND, craft_guidance
    from novelvideo.screenplay_semantics.prompts import SYSTEM_PROMPT
    from novelvideo.media_capabilities.video.h3_prompt_profile import H3_DIRECTOR_SYSTEM_PROMPT
    items = builtin_methods()
    assert {i['subtask_id'] for i in items if i['role_id'] == 'writer'} == set(CRAFT_BY_KIND)
    assert next(i for i in items if i['role_id'] == 'script_parser')['content'] == SYSTEM_PROMPT
    assert next(i for i in items if i['role_id'] == 'video_director')['content'] == H3_DIRECTOR_SYSTEM_PROMPT
    for item in items:
        assert item['source'] and item['read_only']
        assert item['content_hash'] == sha256(item['content'].encode()).hexdigest()
        if item['kind'] == 'protocol':
            assert not item['replaceable']
        else:
            assert item['content'] == craft_guidance(item['subtask_id'])
            assert item['replaceable']


def test_authenticated_inspector_has_no_publish_route():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes.agent_teams import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_api_user] = lambda: {'username': 'alice'}
    client = TestClient(app)
    response = client.get('/agent-team-builtin-methods')
    assert response.status_code == 200
    assert len(response.json()) == 18
    assert client.put('/agent-team-builtin-methods', json={}).status_code == 405
