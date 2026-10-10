from pathlib import Path

from novelvideo.technique_library import adapters


def test_markdown_fence_preserves_multiline_body_not_gallery_excerpt():
    text = """# Original Fixture
- **Source:** reconstructed by apimodels.app
- **Reference clip:** [@tester](https://example.com/clip)
- **In the gallery:** https://apimodels.app/minimax-h3-prompts#prompt-original

## Prompt

```text
镜头缓慢前推。

人物停步回望。
```
"""
    row = adapters._markdown_row(text, adapters.SOURCES[2][1], 'a' * 40, 'prompts/test.md')
    assert row['prompts'][0]['text'] == '镜头缓慢前推。\n\n人物停步回望。\n'
    assert row['prompts'][0]['provenance'] == 'reconstructed'
    gallery = text.split('## Prompt')[0] + '<strong>Prompt</strong> — 此处是截断预览…'
    assert adapters._markdown_row(gallery, adapters.SOURCES[2][1], 'a' * 40, 'prompts/GALLERY.md')['prompts'] == []


def test_archive_digest_is_explicit_and_changes_with_content(tmp_path):
    (tmp_path / 'prompts').mkdir()
    file = tmp_path / 'prompts/a.json'
    file.write_text('{"prompt":"原创夹具"}')
    first = adapters.archive_revision(tmp_path, 'beatapi')
    assert first.startswith('archive-sha256:') and len(first) == 79
    file.write_text('{"prompt":"新原创夹具"}')
    assert adapters.archive_revision(tmp_path, 'beatapi') != first


def test_gallery_recovers_full_body_from_same_post_and_matching_excerpt():
    prefix = 'A quiet classroom with soft morning light. The camera slowly follows a student toward the doorway. '
    body = prefix + 'The student pauses and turns back.'
    donor = adapters._json_row(dict(slug='original', title='Original', prompt=body,
        promptVisibility='author', source={'url': 'https://x.com/author/status/123'}),
        'beatapi', adapters.SOURCES[0][1], 'a' * 40, 'prompts/original.json', [])
    gallery = '''### 1. Classroom
<strong>Prompt</strong> — %s…
[**Read the full prompt →**](https://apimodels.app/minimax-h3-prompts#prompt-classroom) (130 chars, author's own)
**Source:** [author](https://x.com/author/status/123)
''' % prefix
    row = adapters._markdown_row(gallery, adapters.SOURCES[2][1], 'b' * 40, 'prompts/GALLERY.md')
    adapters.recover_gallery_prompts(row, gallery, [donor])
    assert row['prompts'][0]['text'] == body
    assert row['prompts'][0]['repository'] == adapters.SOURCES[0][1]
    assert row['prompts'][0]['path'] == 'prompts/original.json'
    row['prompts'] = []
    wrong = {**donor, 'prompts': [{**donor['prompts'][0], 'text': 'Different clip in the same post'}]}
    adapters.recover_gallery_prompts(row, gallery, [wrong])
    assert row['prompts'] == []
    donor['sources'][0]['url'] = 'https://x.com/author/status/456'
    adapters.recover_gallery_prompts(row, gallery, [donor])
    assert row['prompts'] == []


def test_gallery_recovers_exact_short_prompt():
    body = 'Would you like to dance?'
    donor = adapters._json_row(dict(slug='dance', title='Dance', prompt=body,
        promptVisibility='author', source={'url': 'https://x.com/author/status/123'}),
        'beatapi', adapters.SOURCES[0][1], 'a' * 40, 'prompts/dance.json', [])
    row = {**donor, 'prompts': []}
    adapters.recover_gallery_prompts(row, '<strong>Prompt</strong> — ' + body, [donor])
    assert row['prompts'][0]['text'] == body
