"""Fail-closed native isolation for team CLI workers.

Credentials deliberately given to a CLI are readable by that CLI. The boundary
protects server state and other projects, not secrets entrusted to the worker.
No inherited environment, host root, project tree or administrator home is mounted.
"""
from __future__ import annotations

import json
import ast
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping, Iterable


def isolated_env(scratch: Path, *, runtime: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    scratch = scratch.resolve()
    home = scratch / 'home'
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = scratch / 'tmp'
    temporary.mkdir(mode=0o700, exist_ok=True)
    env = {'HOME': str(home), 'TMPDIR': str(temporary), 'TMP': str(temporary),
           'TEMP': str(temporary), 'LANG': 'en_US.UTF-8',
           'PATH': '/usr/bin:/bin', 'USER': 'worker', 'LOGNAME': 'worker', 'XDG_CONFIG_HOME': str(home / '.config'),
           'XDG_CACHE_HOME': str(home / '.cache')}
    selected = {'codex': ('OPENAI_API_KEY',),
                'workbuddy': ('CODEBUDDY_API_KEY', 'CODEBUDDY_AUTH_TOKEN'),
                'deepseek_harness': ('DEEPSEEK_API_KEY', 'DSH_API_KEY')}.get(runtime, ())
    for key in (*selected, 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'):
        if source.get(key):
            env[key] = source[key]
    if runtime == 'codex':
        target = home / '.codex'
        target.mkdir(mode=0o700, exist_ok=True)
        env['CODEX_HOME'] = str(target)
        auth = Path(source.get('CODEX_HOME') or str(Path.home() / '.codex')) / 'auth.json'
        if env.get('OPENAI_API_KEY'):
            (target / 'auth.json').write_text(json.dumps({'OPENAI_API_KEY': env['OPENAI_API_KEY']}))
            (target / 'auth.json').chmod(0o600)
        elif auth.is_file():
            shutil.copyfile(auth, target / 'auth.json')
            (target / 'auth.json').chmod(0o600)
    if runtime == 'workbuddy':
        # Never copy settings wholesale: they can contain hooks, tools and
        # unrelated provider secrets. Support the CLI's documented env auth.
        settings = Path.home() / '.codebuddy' / 'settings.json'
        if settings.is_file():
            try:
                values = json.loads(settings.read_text()).get('env', {})
                for key in selected:
                    if key not in env and isinstance(values.get(key), str):
                        env[key] = values[key]
            except (ValueError, AttributeError, OSError):
                pass
    if runtime == 'deepseek_harness':
        env['DSH_HOME'] = str(home / '.dsh')
    return env


def _runtime_paths(executable: Path, _seen: set[Path] | None = None) -> list[Path]:
    """Find a CLI's installation, never promote an arbitrary bin to its home."""
    executable = executable.resolve()
    seen = _seen if _seen is not None else set()
    if executable in seen:
        return []
    seen.add(executable)
    paths = [executable]
    for parent in executable.parents:
        installed_package = 'node_modules' in parent.parts or 'app.asar.unpacked' in parent.parts
        if (parent / 'pyvenv.cfg').is_file() or (installed_package and (parent / 'package.json').is_file()):
            paths.append(parent)
            if (parent / 'pyvenv.cfg').is_file():
                # Editable Python installs expose only their declared modules,
                # never the checkout root (which can contain .env and state).
                for finder in parent.glob('lib/python*/site-packages/__editable__*_finder.py'):
                    try:
                        tree = ast.parse(finder.read_text())
                        for item in tree.body:
                            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == 'MAPPING':
                                for value in ast.literal_eval(item.value).values():
                                    module = Path(value)
                                    if module.with_suffix('.py').is_file():
                                        paths.append(module.with_suffix('.py'))
                                    elif module.is_dir():
                                        paths.append(module)
                    except (OSError, ValueError, SyntaxError, TypeError):
                        pass
            break
        if parent == Path.home() or parent == Path('/'):
            break
    # Python launchers reference an absolute interpreter in their shebang.
    try:
        with executable.open('rb') as stream:
            header = stream.read(4096)
            line = header.split(b'\n', 1)[0]
        if line.startswith(b'#!'):
            words = line[2:].decode().split()
            interpreter = words[0]
            if interpreter == '/usr/bin/env' and len(words) > 1:
                interpreter = shutil.which(words[1]) or words[1]
            candidate = Path(interpreter)
            if candidate.is_absolute() and candidate.is_file() and candidate.resolve() != executable:
                paths.extend(_runtime_paths(candidate, seen))
            # Common administrator shell launcher: follow only a literal,
            # absolute exec target, never expand shell expressions.
            if Path(interpreter).name in {'sh', 'bash', 'zsh'}:
                match = re.search(r'^exec "(/[^"$`\n]+)" "\$@"\s*$', header.decode(), re.M)
                if match:
                    paths.extend(_runtime_paths(Path(match.group(1)), seen))
    except (OSError, UnicodeError):
        pass
    if executable.name.startswith('python'):
        # Runtime's own stdlib, not the enclosing server repository.
        prefix = executable.parent.parent
        if (prefix / 'lib').is_dir():
            paths.append(prefix / 'lib')
    if platform.system() == 'Darwin' and executable.suffix not in {'.js', '.py'}:
        paths.extend(_darwin_libraries(executable))
    return paths


def _darwin_libraries(binary: Path) -> list[Path]:
    """Resolve exact non-system Mach-O dependencies (including Homebrew)."""
    found: set[Path] = set()
    pending = [binary]
    while pending:
        current = pending.pop()
        try:
            with current.open('rb') as stream:
                if stream.read(4) not in {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe'}:
                    continue
            output = subprocess.run(['/usr/bin/otool', '-L', str(current)],
                                    capture_output=True, text=True, timeout=5,
                                    env={'PATH': '/usr/bin:/bin'}).stdout
            for line in output.splitlines()[1:]:
                name = line.strip().split(' (', 1)[0]
                if name.startswith(('/usr/lib/', '/System/')):
                    continue
                candidates = [Path(name)] if name.startswith('/') else []
                if name.startswith('@loader_path/'):
                    candidates = [current.parent / name.removeprefix('@loader_path/')]
                if name.startswith('@executable_path/'):
                    candidates = [binary.parent / name.removeprefix('@executable_path/')]
                if name.startswith('@rpath/'):
                    candidates = [current.parent / name.removeprefix('@rpath/'),
                                  binary.parent.parent / 'lib' / name.removeprefix('@rpath/')]
                for path in candidates:
                    if path.is_file() and path.resolve() not in found:
                        found.add(path.resolve())
                        pending.append(path.resolve())
                        break
        except (OSError, subprocess.TimeoutExpired):
            continue
    return list(found)


def wrap_isolated_command(cmd: list[str], scratch: Path, *, read_paths: Iterable[Path] = ()) -> list[str]:
    scratch = scratch.resolve(strict=True)
    binary = shutil.which(cmd[0]) if not Path(cmd[0]).is_absolute() else cmd[0]
    if not binary:
        raise RuntimeError(f'Team sandbox cannot locate executable: {cmd[0]}')
    command = [str(Path(binary).resolve()), *cmd[1:]]
    runtime_paths = _runtime_paths(Path(binary))
    # Only fixed OS runtime directories and selected non-secret resolver files.
    system_paths = ['/usr/bin', '/bin', '/usr/lib', '/lib', '/lib64',
                    '/usr/share/zoneinfo', '/usr/share/ca-certificates', '/etc/ssl/certs', '/etc/resolv.conf',
                    '/etc/hosts', '/etc/nsswitch.conf', '/etc/ld.so.cache', '/etc/passwd', '/etc/group']
    system = platform.system()
    if system == 'Darwin':
        system_paths += ['/System/Library', '/System/Volumes/Preboot/Cryptexes/OS', '/Library/Apple', '/usr/share/locale',
                         '/private/etc/ssl', '/private/etc/resolv.conf', '/private/etc/hosts',
                         '/dev/null', '/dev/urandom', '/dev/random']
    selected_paths = [*(Path(v) for v in system_paths), *runtime_paths, *read_paths]
    paths = sorted({str(p.resolve()) for p in selected_paths if p.exists()})
    if system == 'Darwin':
        if not Path('/usr/bin/sandbox-exec').is_file():
            raise RuntimeError('Team sandbox requires /usr/bin/sandbox-exec; refusing unsandboxed execution')
        def rule(p: str) -> str:
            return f'(subpath {json.dumps(p)})'
        profile = '\n'.join([
            '(version 1)', '(deny default)',
            '(allow process-exec process-fork)', '(allow signal (target self))',
            '(allow process-info* (target same-sandbox))',
            '(allow sysctl-read)', '(allow mach-lookup)', '(allow network-outbound)',
            '(allow file-read-metadata)',
            '(allow file-read-data (literal "/"))',
            '(allow file-read* ' + ' '.join(rule(p) for p in [*paths, str(scratch)]) + ')',
            '(allow file-write* ' + rule(str(scratch)) + ' (literal "/dev/null"))',
        ])
        return ['/usr/bin/sandbox-exec', '-p', profile, '--', *command]
    if system == 'Linux':
        bwrap = shutil.which('bwrap')
        if not bwrap:
            raise RuntimeError('Team sandbox requires bubblewrap (bwrap) and enabled user namespaces; refusing unsandboxed execution')
        argv = [bwrap, '--die-with-parent', '--new-session', '--unshare-pid',
                '--unshare-ipc', '--unshare-uts', '--unshare-user',
                '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev']
        # bubblewrap creates missing destination parents; no host root bind.
        for value in paths:
            argv.extend(['--ro-bind', value, value])
        for path in selected_paths:
            # Resolver/certificate aliases must exist at their original names.
            if path.exists() and path.is_symlink() and str(path) not in {'/bin', '/lib', '/lib64'} and str(path) != str(path.resolve()):
                argv.extend(['--ro-bind', str(path.resolve()), str(path)])
        # Preserve system symlink aliases (e.g. /bin -> /usr/bin).
        for alias in ['/bin', '/lib', '/lib64']:
            path = Path(alias)
            if path.is_symlink():
                argv.extend(['--symlink', str(path.resolve()), alias])
        argv.extend(['--bind', str(scratch), str(scratch), '--chdir', str(scratch), '--', *command])
        return argv
    raise RuntimeError(f'Team sandbox is unavailable on {system}; refusing unsandboxed execution')


def prepare_cli(cmd: list[str], scratch: Path, *, runtime: str) -> tuple[list[str], dict[str, str]]:
    env = isolated_env(scratch, runtime=runtime)
    # /usr/bin/env node launchers need only the selected node binary's directory.
    node = shutil.which('node')
    paths = []
    if node:
        node = str(Path(node).resolve())
        env['PATH'] = f'{Path(node).parent}:' + env['PATH']
        paths = _runtime_paths(Path(node))
    return wrap_isolated_command(cmd, scratch, read_paths=paths), env


def main() -> int:
    """Unpaid deployment preflight: native allow/deny and installed CLI startup."""
    try:
        with tempfile.TemporaryDirectory(prefix='nuomi-isolation-probe-') as temporary:
            root = Path(temporary)
            scratch = root / 'worker'
            scratch.mkdir()
            secret = root / 'server-secret'
            secret.write_text('server-only')
            (scratch / 'input').write_text('worker-only')
            script = (
                'from pathlib import Path; import sys; '
                'p=Path(sys.argv[1]); s=Path(sys.argv[2]); '
                'assert (p/"input").read_text()=="worker-only"; '
                '(p/"output").write_text("ok"); '
                '\ntry: s.read_text()\nexcept (PermissionError,FileNotFoundError): pass\nelse: raise AssertionError("server read escaped sandbox")'
                '\ntry: s.write_text("changed")\nexcept (PermissionError,FileNotFoundError): pass\nelse: raise AssertionError("server write escaped sandbox")'
            )
            env = isolated_env(scratch, runtime='probe', environ={})
            checks = [('filesystem', [sys.executable, '-I', '-c', script, str(scratch), str(secret)])]
            for name, variable in [('node', 'NODE_BIN'), ('codex', 'CODEX_BIN'),
                                   ('workbuddy', 'WORKBUDDY_BIN'), ('dsh', 'DSH_BIN'),
                                   ('hermes', 'HERMES_CLI_PATH')]:
                binary = os.getenv(variable) or shutil.which(name)
                if binary:
                    checks.append((name, [binary, '--version']))
            for name, cmd in checks:
                wrapped, selected_env = prepare_cli(cmd, scratch, runtime='probe')
                result = subprocess.run(wrapped, cwd=scratch, env=selected_env or env,
                                        capture_output=True, timeout=30)
                if result.returncode:
                    print(f'FAIL: {name} sandbox startup (exit {result.returncode}): '
                          + result.stderr.decode(errors='replace')[-1000:], file=sys.stderr)
                    return 1
                print(f'PASS: {name}')
            assert secret.read_text() == 'server-only'
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'FAIL: team sandbox preflight: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
