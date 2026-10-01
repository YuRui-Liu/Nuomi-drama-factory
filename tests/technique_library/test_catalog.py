import importlib.util
import json

import pytest


def module():
    assert importlib.util.find_spec('novelvideo.technique_library'), 'case library is required'
    from novelvideo.technique_library import catalog
    return catalog


def test_missing_catalog_is_explicit(tmp_path, monkeypatch):
    catalog = module()
    monkeypatch.setattr(catalog, 'CATALOG_PATH', tmp_path / 'missing.json')
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.query_cases()


def test_paging_rejects_invalid_arguments():
    catalog = module()
    for arguments in ({'offset': -1}, {'limit': 101}, {'limit': 0}):
        with pytest.raises(ValueError):
            catalog.query_cases(**arguments)


def sample_case(index=0):
    return {'id': f'h3-{index:016x}', 'title': f'动作 {index}', 'summary': '围绕动作设计镜头与节奏。',
            'use_cases': ['action'], 'provenance': 'author',
            'sources': [{'repository': 'test/repo', 'revision': 'a'*40, 'path': 'a.json',
                         'upstream_id': str(index), 'url': 'https://example.com/a'}]}


def test_offline_filter_and_paging(tmp_path, monkeypatch):
    catalog = module()
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'schema_version': '1.0', 'sources': [], 'report': {},
                               'cases': [sample_case(i) for i in range(3)]}))
    monkeypatch.setattr(catalog, 'CATALOG_PATH', path)
    result = catalog.query_cases(q='动作', use_case='action', provenance='author', offset=1, limit=1)
    assert result['total'] == 3
    assert result['items'][0]['id'] == sample_case(1)['id']
    assert catalog.get_case('missing') is None
    assert catalog.query_cases(provenance='official')['total'] == 0
    data = json.loads(path.read_text())
    data['cases'][0]['media_url'] = 'javascript:alert(1)'
    path.write_text(json.dumps(data))
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.query_cases()


def test_unchanged_catalog_reuses_validation(tmp_path, monkeypatch):
    catalog = module()
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'schema_version': '1.0', 'sources': [], 'report': {}, 'cases': [sample_case()]}))
    monkeypatch.setattr(catalog, 'CATALOG_PATH', path)
    original = type(path).read_text
    reads = []
    def read(self, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(type(path), 'read_text', read)
    catalog.query_cases()
    catalog.query_cases()
    assert reads == [path]
