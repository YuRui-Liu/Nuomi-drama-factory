from novelvideo.creation.agent_context import node_snapshot, production_node_context


def test_professional_context_exposes_bound_inputs_without_arbitrary_fields():
    node = {'id': 'n', 'type': 'screenplayNode', 'data': {
        'episodeNumber': 2, 'documentId': 'doc', 'documentRevisionId': 'rev',
        'planRunId': 'plan', 'unrelatedCredential': 'do not expose',
        'screenplayOperationInputs': {'operation': 'rewrite', 'start': 0, 'end': 2,
                                     'scope': 'selection', 'mode': 'dialogue', 'instruction': '简洁', 'preserve': ''},
    }}
    context = node_snapshot(node)
    assert context['production_context']['documentRevisionId'] == 'rev'
    assert 'unrelatedCredential' not in context['production_context']
    assert context['editable_fields']['screenplayOperationInputs']['mode'] == 'dialogue'
    assert 'documentRevisionId' not in context['editable_fields']


def test_inspected_content_is_not_silently_truncated_or_shared_mutably():
    node = {'type': 'screenplayNode', 'data': {'workflowResult': {'markdown': '正文'}}}
    result = production_node_context(node)
    result['workflowResult']['markdown'] = 'changed'
    assert node['data']['workflowResult']['markdown'] == '正文'
    node['data']['workflowResult']['markdown'] = '文' * 100001
    result = production_node_context(node)
    assert result['unavailable_fields'] == ['workflowResult']
    assert 'workflowResult' not in result


def test_voice_context_advertises_editable_leaves_without_identity_controls():
    node = {'id': 'voice', 'type': 'characterVoiceNode', 'data': {
        'characterId': 'character-a', 'voiceVersionId': 'adopted-a',
        'voiceDesign': {'voice_spec': {'pitch': '中低音'}, 'performance': {},
                        'audition_text': '别出声。', 'audition_source': 'example'},
    }}
    editable = node_snapshot(node)['editable_fields']
    assert editable['voiceDesign']['voice_spec']['pitch'] == '中低音'
    assert editable['voiceDesign']['audition_text'] == '别出声。'
    assert 'audition_source' not in editable['voiceDesign']
    assert 'voiceVersionId' not in editable
