"""Exercise the real HTTP payload against a thinking-mode compatible stub."""
import json

import httpx
import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from novelvideo.freezone import text_node


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['script', 'translation'])
async def test_canvas_text_output_does_not_force_tools_and_repairs_invalid_json(monkeypatch, kind):
    requests = []
    expected = (
        {'title': '雨夜', 'rows': [{'shot_no': 1, 'duration': 5, 'visual_description': '邮差递信'}]}
        if kind == 'script' else
        {'translated_text': 'Rainy night', 'source_language': 'zh', 'target_language': 'en'}
    )

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get('tool_choice'):
            return httpx.Response(400, json={'error': {
                'message': 'Thinking mode does not support this tool_choice',
                'type': 'invalid_request_error',
            }})
        output = {'rows': [{'shot_no': 'invalid'}]} if kind == 'script' else {'source_language': 'invalid'}
        if len(requests) > 1:
            output = expected
        return httpx.Response(200, json={
            'id': 'test', 'object': 'chat.completion', 'created': 0,
            'model': 'deepseek-v4-flash-vision-exp',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': json.dumps(output)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = OpenAIChatModel('deepseek-v4-flash-vision-exp', provider=OpenAIProvider(
            base_url='https://example.test/v1', api_key='test-only', http_client=client))
        monkeypatch.setattr('novelvideo.config.get_newapi_text_pydantic_model', lambda *a, **kw: model)
        factory = text_node.create_freezone_story_script_agent if kind == 'script' else text_node.create_freezone_translation_agent
        result = await factory().run('雨夜，邮差递信')

    assert len(requests) == 2
    assert all('tool_choice' not in request and not request.get('tools') for request in requests)
    assert all('thinking' not in request for request in requests)
    if kind == 'script':
        assert result.output.rows[0].shot_no == 1
        assert result.output.rows[0].visual_description == '邮差递信'
    else:
        assert result.output.translated_text == 'Rainy night'
