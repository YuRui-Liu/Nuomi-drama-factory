from pathlib import Path
import json
import os
import platform
import subprocess

import pytest

from novelvideo.team.execution import isolated_env, wrap_isolated_command


def test_environment_is_allowlist(tmp_path):
    env = isolated_env(tmp_path, runtime='deepseek_harness', environ={
        'ST_TEAM_DATABASE_URL': 'secret', 'OPENAI_API_KEY': 'other',
        'DEEPSEEK_API_KEY': 'selected', 'PYTHONPATH': '/server',
        'NODE_OPTIONS': '--require=/server/evil.js', 'HOME': '/server',
    })
    assert env['DEEPSEEK_API_KEY'] == 'selected'
    assert env['HOME'] == str(tmp_path / 'home')
    assert not {'ST_TEAM_DATABASE_URL', 'OPENAI_API_KEY', 'PYTHONPATH', 'NODE_OPTIONS'} & env.keys()


def test_codex_api_key_auth_is_selected_not_shared_history(tmp_path):
    env = isolated_env(tmp_path, runtime='codex', environ={'OPENAI_API_KEY': 'test-only'})
    home = Path(env['CODEX_HOME'])
    assert json.loads((home / 'auth.json').read_text()) == {'OPENAI_API_KEY': 'test-only'}
    assert set(p.name for p in home.iterdir()) == {'auth.json'}


def test_project_package_json_does_not_grant_project_root(tmp_path):
    from novelvideo.team.execution import _runtime_paths
    (tmp_path / 'package.json').write_text('{}')
    binary = tmp_path / 'scripts' / 'cli'
    binary.parent.mkdir()
    binary.write_text('#!/bin/sh\nexit 0\n')
    assert tmp_path not in _runtime_paths(binary)


def test_missing_sandbox_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.team.execution.platform.system', lambda: 'Windows')
    with pytest.raises(RuntimeError, match='sandbox'):
        wrap_isolated_command(['/bin/echo', 'hi'], tmp_path)


def test_macos_has_no_global_read(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.team.execution.platform.system', lambda: 'Darwin')
    argv = wrap_isolated_command(['/bin/echo', 'hi'], tmp_path)
    assert argv[:2] == ['/usr/bin/sandbox-exec', '-p']
    assert '(deny default)' in argv[2]
    assert '(allow file-read* (subpath "/"))' not in argv[2]
    assert f'(subpath "{tmp_path.resolve()}")' in argv[2]


def test_linux_uses_empty_root_not_host_root(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.team.execution.platform.system', lambda: 'Linux')
    monkeypatch.setattr('novelvideo.team.execution.shutil.which', lambda name: '/usr/bin/bwrap' if name == 'bwrap' else None)
    argv = wrap_isolated_command(['/bin/echo', 'hi'], tmp_path)
    assert '--unshare-pid' in argv
    assert '--die-with-parent' in argv
    assert not any(argv[i:i+3] == ['--ro-bind', '/', '/'] for i in range(len(argv)))
    assert not any(argv[i:i+3] == ['--ro-bind', '/etc', '/etc'] for i in range(len(argv)))


@pytest.mark.skipif(platform.system() != 'Darwin' or os.getenv('RUN_NATIVE_SANDBOX_TESTS') != '1', reason='requires native macOS sandbox permission')
def test_native_macos_filesystem_boundary(tmp_path):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    secret = tmp_path / 'server-secret'
    secret.write_text('not-for-the-worker')
    own = scratch / 'input'
    own.write_text('allowed')
    env = isolated_env(scratch, runtime='test', environ={})
    cmd = ['/bin/sh', '-c', 'cat "$1" && ! cat "$2" && ! echo stolen > "$2" && echo ok > "$3"',
           'probe', str(own), str(secret), str(scratch / 'result')]
    result = subprocess.run(wrap_isolated_command(cmd, scratch), env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b'allowed'
    assert secret.read_text() == 'not-for-the-worker'
    assert (scratch / 'result').read_text().strip() == 'ok'
