import hashlib

import pytest

from novelvideo.technique_library.models import Case


def source():
    return dict(repository='example/cases', revision='a' * 40, path='prompts/demo.json',
                upstream_id='demo', url='https://example.com/demo', provenance='author')


def test_preserves_exact_text_and_explicit_language():
    from novelvideo.technique_library.prompt_records import prompt_record
    text = '镜头缓慢前推。\n\n人物停步回望。\n'
    record = prompt_record(text, source(), language='zh')
    assert record.text == text
    assert record.language == 'zh'
    assert record.content_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert record.provenance == 'author'


@pytest.mark.parametrize('text', ['', '   ', 'https://example.com/prompt', 'Not published', '未公开'])
def test_no_body_for_empty_links_or_placeholders(text):
    from novelvideo.technique_library.prompt_records import prompt_record
    assert prompt_record(text, source()) is None


def test_unpublished_claim_never_becomes_public_prompt():
    from novelvideo.technique_library.prompt_records import prompt_record
    assert prompt_record('Please visit the creator.', {**source(), 'provenance': 'unpublished'}) is None


def test_legacy_case_defaults_to_empty_prompts():
    case = Case(id='h3-' + '0' * 16, title='Demo', summary='Summary', use_cases=[],
                provenance='author', sources=[source()])
    assert case.prompts == []


def test_merge_preserves_distinct_source_bodies_without_duplicates():
    from novelvideo.technique_library.prompt_records import prompt_record
    from novelvideo.technique_library.ingest import merge_cases
    first = source()
    second = {**first, 'repository': 'another/cases', 'provenance': 'reconstructed'}
    def row(claim, text):
        return dict(title='Demo', summary='Summary', use_cases=[], provenance=claim['provenance'],
                    sources=[claim], media_url='https://example.com/demo.mp4',
                    prompts=[prompt_record(text, claim).model_dump()])
    rows = [row(first, '原创测试镜头。'), row(second, '原创测试重构镜头。')]
    cases, _ = merge_cases(rows + [rows[0]])
    assert len(cases) == 1
    assert [p.text for p in cases[0].prompts] == ['原创测试镜头。', '原创测试重构镜头。']
    assert [p.provenance for p in cases[0].prompts] == ['author', 'reconstructed']
