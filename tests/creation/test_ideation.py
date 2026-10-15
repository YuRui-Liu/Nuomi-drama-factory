from copy import deepcopy

import pytest
from novelvideo.creation.ideation import validate_candidates

_MISSING = object()


def candidate(identifier='idea-1', **changes):
    return {
        'id':identifier,
        'title':'雾港来信',
        'premise':'一封迟到十年的信，揭开海港小城的旧案。',
        'protagonist_goal':'找到寄信人并救回家中的旧书店。',
        'conflict':'镇民守护的秘密让每条线索都指向主角的家人。',
        'hook':'每封信都预告下一场尚未发生的事故。',
        'expansion':'线索可扩展为多集调查，并逐步改写家族关系。',
        **changes,
    }


def payload(candidates=_MISSING, **changes):
    if candidates is _MISSING:
        candidates = [candidate(f'idea-{i}') for i in range(3)]
    return {'schema_version':1,'candidates':candidates,**changes}


def test_valid_candidates_are_trimmed_without_mutating_input():
    source = payload([candidate(' idea-1 ', title=' 雾港来信 '), candidate('idea-2'), candidate('idea-3')])
    original = deepcopy(source)

    result = validate_candidates(source)

    assert result[0]['id'] == 'idea-1'
    assert result[0]['title'] == '雾港来信'
    assert len(result) == 3
    assert source == original


@pytest.mark.parametrize('schema_version', [True, False, 1.0, '1', 2, None])
def test_rejects_non_integer_or_unsupported_schema_version(schema_version):
    with pytest.raises(ValueError):
        validate_candidates(payload(schema_version=schema_version))


@pytest.mark.parametrize('value', [None, [], 'json', 3])
def test_rejects_non_object_payload(value):
    with pytest.raises(ValueError):
        validate_candidates(value)


def test_rejects_unknown_root_fields():
    with pytest.raises(ValueError):
        validate_candidates(payload(extra='not allowed'))


@pytest.mark.parametrize('missing', ['schema_version','candidates'])
def test_rejects_missing_root_fields(missing):
    body = payload()
    del body[missing]
    with pytest.raises(ValueError):
        validate_candidates(body)


@pytest.mark.parametrize('items', [None, (), [], [candidate('one'), candidate('two')],
                                    [candidate(str(i)) for i in range(4)]])
def test_requires_exactly_three_candidate_objects(items):
    with pytest.raises(ValueError):
        validate_candidates(payload(items))


def test_rejects_unknown_candidate_fields():
    with pytest.raises(ValueError):
        validate_candidates(payload([candidate(extra='not allowed'), candidate('idea-2'), candidate('idea-3')]))


def test_rejects_non_object_candidates():
    with pytest.raises(ValueError):
        validate_candidates(payload([None, candidate('idea-2'), candidate('idea-3')]))


@pytest.mark.parametrize('identifier', ['', '   ', None, 7])
def test_rejects_empty_or_non_string_candidate_ids(identifier):
    with pytest.raises(ValueError):
        validate_candidates(payload([candidate(identifier), candidate('idea-2'), candidate('idea-3')]))


def test_rejects_ids_that_duplicate_after_trimming():
    with pytest.raises(ValueError):
        validate_candidates(payload([candidate('idea-1'), candidate(' idea-1 '), candidate('idea-3')]))


@pytest.mark.parametrize('field', ['title','premise','protagonist_goal','conflict','hook','expansion'])
@pytest.mark.parametrize('value', ['', '  \t\n  ', None, 7])
def test_rejects_empty_or_non_string_body_fields(field, value):
    with pytest.raises(ValueError):
        validate_candidates(payload([candidate('idea-1', **{field:value}), candidate('idea-2'), candidate('idea-3')]))


def test_accepts_body_text_at_30000_characters():
    first = candidate('idea-1')
    other_fields = ('title','protagonist_goal','conflict','hook','expansion')
    first['premise'] = 'x' * (30000 - sum(len(first[key]) for key in other_fields))

    assert len(validate_candidates(payload([first, candidate('idea-2'), candidate('idea-3')]))[0]['premise']) > 0


def test_rejects_body_text_over_30000_characters_in_one_candidate():
    first = candidate('idea-1', premise='x' * 30001)
    with pytest.raises(ValueError):
        validate_candidates(payload([first, candidate('idea-2'), candidate('idea-3')]))


def test_rejects_missing_candidate_fields():
    incomplete = candidate('idea-1')
    del incomplete['hook']
    with pytest.raises(ValueError):
        validate_candidates(payload([incomplete, candidate('idea-2'), candidate('idea-3')]))
