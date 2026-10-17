from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('module_name,callback', [
    ('casting_submission', 'journal'),
    ('casting_store', 'store'),
    ('casting_recovery', 'recovery'),
])
def test_windows_casting_writes_do_not_open_directory(tmp_path, monkeypatch, module_name, callback):
    import importlib
    import os

    module = importlib.import_module('novelvideo.character_visual.' + module_name)
    def directory_open(*args, **kwargs):
        raise PermissionError('Windows CRT cannot open a directory')
    proxy = SimpleNamespace(**{key: getattr(os, key) for key in dir(os)})
    proxy.name = 'nt'
    proxy.open = directory_open
    monkeypatch.setattr(module, 'os', proxy)
    if callback == 'journal':
        journal = module.SubmissionJournal(SimpleNamespace(state_dir=tmp_path, project_id='p'))
        journal.write({'character': '林澈'})
        assert journal.read() == {'character': '林澈'}
    elif callback == 'store':
        store = module.CastingCandidateStore(tmp_path, state_dir=tmp_path, project_id='p')
        store._write({'character': '林澈'})
        assert store._read() == {'character': '林澈'}
    else:
        path = tmp_path / 'recovery.json'
        module.durable_write(path, b'persisted')
        assert path.read_bytes() == b'persisted'
