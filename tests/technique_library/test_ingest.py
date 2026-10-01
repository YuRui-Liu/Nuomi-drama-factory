import importlib.util
import json

import pytest


@pytest.fixture(autouse=True)
def synthetic_snapshots(monkeypatch):
    # Fixtures intentionally model source formats, not production Git identities.
    try:
        from novelvideo.technique_library import adapters
        monkeypatch.setattr(adapters, 'verify_snapshot', lambda *args: None)
    except (ImportError, AttributeError):
        pass


def ingest_module():
    assert importlib.util.find_spec('novelvideo.technique_library.ingest'), 'offline importer is required'
    from novelvideo.technique_library import ingest
    return ingest


def test_failure_preserves_existing_output(tmp_path):
    ingest = ingest_module()
    output = tmp_path / 'catalog.json'
    output.write_text('previous approved bundle')
    with pytest.raises(ingest.IngestError):
        ingest.import_catalog(tmp_path / 'missing', output)
    assert output.read_text() == 'previous approved bundle'


def test_post_normalization_preserves_distinct_media():
    ingest = ingest_module()
    assert ingest.normalize_url('https://twitter.com/a/status/123?s=20') == 'https://x.com/i/status/123'
    assert ingest.normalize_url('https://x.com/b/status/123/video/1') != ingest.normalize_url('https://x.com/b/status/123/video/2')


def fixture_sources(root):
    beat = root / 'beatapi/prompts'
    sky = root / 'skynotsilent/data'
    stim = root / 'stimqq/prompts/action'
    for directory in (beat, sky, stim):
        directory.mkdir(parents=True)
    (beat / 'catalog.json').write_text(json.dumps({'prompts': [{'slug': 'one'}]}))
    (beat / 'one.json').write_text(json.dumps({'slug': 'one', 'title': {'zh': '动作'}, 'category': 'action',
        'source': {'url': 'https://twitter.com/a/status/123', 'name': '@a'}, 'promptVisibility': 'same-post',
        'video': 'https://media.example/a.mp4', 'prompt': 'PRIVATE_FULL_PROMPT', 'description': 'COPYRIGHT_SUMMARY'}))
    (sky / 'cases.json').write_text(json.dumps([{'id': 'one', 'title': 'Action', 'category': 'action-vfx',
        'sourceUrl': 'https://x.com/a/status/123', 'author': '@a', 'mediaUrl': 'https://media.example/a.mp4',
        'promptProvenance': 'reconstructed', 'verified': True, 'summary': 'COPYRIGHT_SUMMARY', 'prompt': 'PRIVATE_FULL_PROMPT'},
        {'id': 'two', 'title': 'Other clip', 'sourceUrl': 'https://x.com/a/status/123',
         'mediaUrl': 'https://media.example/b.mp4', 'promptProvenance': 'not-published'}]))
    (stim.parent / 'GALLERY.md').write_text('## Action\n### 1. Demo\n[Read](https://apimodels.app/minimax-h3-prompts#prompt-demo) (author\'s own)\n**Source:** [@b](https://x.com/b/status/456) · 15s\n')
    (stim / 'demo.md').write_text('# Demo\n- **Category:** Action\n- **Source:** author\'s own\n- **Reference clip:** [@b](https://x.com/b/status/456)\n- **In the gallery:** https://apimodels.app/minimax-h3-prompts#prompt-demo\n\n## Prompt\nPRIVATE_FULL_PROMPT')


def test_metadata_only_merge_counts_and_idempotence(tmp_path):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    output = tmp_path / 'out.json'
    bundle = ingest.import_catalog(tmp_path, output)
    assert bundle['report']['enumerated'] == 5
    assert bundle['report']['parsed'] == 5
    assert bundle['report']['merged'] == 2
    assert bundle['report']['final'] == 3
    assert bundle['report']['errors'] == 0
    assert len(bundle['cases']) == 3
    serialized = output.read_text()
    assert 'PRIVATE_FULL_PROMPT' not in serialized and 'COPYRIGHT_SUMMARY' not in serialized
    merged = next(c for c in bundle['cases'] if c['media_url'] == 'https://media.example/a.mp4')
    assert len(merged['sources']) == 2
    assert merged['provenance'] == 'unknown' and 'provenance' in merged['conflicts']
    assert merged['sources'][1]['source_verification'] is True
    assert merged['sources'][1]['prompt_provenance'] == 'reconstructed'
    assert merged['authorization_status'] == 'unknown'
    assert merged['imported_at']
    assert bundle['report']['conflicts']['count'] >= 1
    assert bundle['report']['duplicate_candidates']
    gallery = next(c for c in bundle['cases'] if any(s['upstream_id'] == 'demo' for s in c['sources']))
    assert gallery['conflicts'] == []
    assert gallery['duration'] == '15s'
    assert all(c['local_verification'] == 'unverified' for c in bundle['cases'])
    ingest.import_catalog(tmp_path, output)
    assert serialized == output.read_text()
    (tmp_path / 'beatapi/prompts/bad.json').write_text('{')
    with pytest.raises(ingest.IngestError):
        ingest.import_catalog(tmp_path, output)
    assert serialized == output.read_text()


@pytest.mark.parametrize('invalid', ['javascript:alert(1)', 'data:video/mp4;base64,abc', 'https://user:secret@example.com/video'])
def test_unsafe_media_refuses_publish(tmp_path, invalid):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    path = tmp_path / 'beatapi/prompts/one.json'
    item = json.loads(path.read_text())
    item['video'] = invalid
    path.write_text(json.dumps(item))
    output = tmp_path / 'out.json'
    output.write_text('old')
    with pytest.raises(ingest.IngestError):
        ingest.import_catalog(tmp_path, output)
    assert output.read_text() == 'old'


def test_empty_source_refuses_publish(tmp_path):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    (tmp_path / 'skynotsilent/data/cases.json').write_text('[]')
    with pytest.raises(ingest.IngestError):
        ingest.import_catalog(tmp_path, tmp_path / 'out.json')
    assert not (tmp_path / 'out.json').exists()


def test_missing_indexed_file_refuses_publish(tmp_path):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    (tmp_path / 'beatapi/prompts/catalog.json').write_text(json.dumps({'prompts': [{'slug': 'one'}, {'slug': 'missing'}]}))
    with pytest.raises(ingest.IngestError):
        ingest.import_catalog(tmp_path, tmp_path / 'out.json')


def test_revision_verification_required(tmp_path, monkeypatch):
    from novelvideo.technique_library import adapters
    # Reload restores the real checker replaced for synthetic format fixtures.
    import importlib
    adapters = importlib.reload(adapters)
    with pytest.raises(ValueError, match='snapshot'):
        adapters.verify_snapshot(tmp_path, 'a' * 40)


def test_case_identity_survives_media_changes(tmp_path):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    output = tmp_path / 'out.json'
    first = ingest.import_catalog(tmp_path, output)
    original = next(case for case in first['cases'] if any(s['upstream_id'] == 'two' for s in case['sources']))
    path = tmp_path / 'skynotsilent/data/cases.json'
    records = json.loads(path.read_text())
    records[1]['mediaUrl'] = 'https://other.example/new-cdn.mp4'
    path.write_text(json.dumps(records))
    second = ingest.import_catalog(tmp_path, output)
    updated = next(case for case in second['cases'] if any(s['upstream_id'] == 'two' for s in case['sources']))
    assert original['id'] == updated['id']
    assert original['imported_at'] == updated['imported_at']


@pytest.mark.parametrize('extra_name', ['untracked.md', 'ignored.md'])
def test_snapshot_rejects_extra_valid_case_even_when_git_diff_is_clean(tmp_path, monkeypatch, extra_name):
    import importlib
    from types import SimpleNamespace
    from novelvideo.technique_library import adapters
    adapters = importlib.reload(adapters)
    fixture_sources(tmp_path)
    base = tmp_path / 'stimqq'
    (base / '.git').mkdir()
    (base / '.gitignore').write_text('prompts/action/ignored.md\n')
    extra = base / 'prompts/action' / extra_name
    extra.write_text((base / 'prompts/action/demo.md').read_text().replace('prompt-demo', 'prompt-extra'))
    revision = adapters.SOURCES[2][2]
    assert adapters._markdown_row(extra.read_text(), adapters.SOURCES[2][1], revision,
                                  extra.relative_to(base).as_posix())['sources'][0]['upstream_id'] == 'extra'
    def clean_git(command, **kwargs):
        args = command[3:]
        output = revision if args[0] == 'rev-parse' else (
            'prompts/GALLERY.md\nprompts/action/demo.md' if args[0] == 'ls-tree' else '')
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(adapters.subprocess, 'run', clean_git)
    with pytest.raises(ValueError, match='extra'):
        adapters.verify_snapshot(base, revision)


def test_report_failure_preserves_previous_catalog(tmp_path):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    output = tmp_path / 'out.json'
    ingest.import_catalog(tmp_path, output)
    previous = output.read_bytes()
    source = tmp_path / 'beatapi/prompts/one.json'
    record = json.loads(source.read_text())
    record['title']['zh'] = '修改标题'
    source.write_text(json.dumps(record))
    blocked_parent = tmp_path / 'not-a-directory'
    blocked_parent.write_text('blocking report parent')
    with pytest.raises(OSError):
        ingest.import_catalog(tmp_path, output, blocked_parent / 'report.json')
    assert output.read_bytes() == previous


def test_report_cannot_share_catalog_path(tmp_path):
    ingest = ingest_module()
    fixture_sources(tmp_path)
    output = tmp_path / 'out.json'
    ingest.import_catalog(tmp_path, output)
    previous = output.read_bytes()
    with pytest.raises(ingest.IngestError, match='different'):
        ingest.import_catalog(tmp_path, output, tmp_path / '.' / 'out.json')
    assert output.read_bytes() == previous


def test_classification_uses_values_not_metadata_field_names():
    from novelvideo.technique_library import adapters
    row = adapters._json_row(
        {'id': 'plain', 'title': '普通场景', 'sourceUrl': 'https://example.com/plain',
         'category': 'daily-life', 'styles': [], 'tags': [], 'scenes': []},
        'skynotsilent', adapters.SOURCES[1][1], adapters.SOURCES[1][2], 'data/cases.json', [])
    assert 'stylized' not in row['use_cases']
    row = adapters._json_row(
        {'id': 'anime', 'title': '动画场景', 'sourceUrl': 'https://example.com/anime',
         'category': 'daily-life', 'styles': ['anime']},
        'skynotsilent', adapters.SOURCES[1][1], adapters.SOURCES[1][2], 'data/cases.json', [])
    assert 'stylized' in row['use_cases']
