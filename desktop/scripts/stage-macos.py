"""Stage a relocatable arm64 runtime; never copy project data or credentials."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'desktop-runtime'


def run(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def copy_tree(source, destination):
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store',
                        '.cognee_system', '.data_storage', '.env', '.env.*'))


def bundle_ffmpeg():
    destination = OUT / 'ffmpeg'
    destination.mkdir()
    copied = {}

    def collect(source):
        source = Path(source).resolve()
        if source in copied:
            return copied[source]
        target = destination / source.name
        if target.exists():
            raise RuntimeError(f'Duplicate native library basename: {source.name}')
        copied[source] = target
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode | 0o200)
        dependencies = run('otool', '-L', source).splitlines()[1:]
        for line in dependencies:
            dependency = line.strip().split(' (', 1)[0]
            if dependency.startswith(('/System/', '/usr/lib/')):
                continue
            if dependency == str(source):
                continue
            if dependency.startswith('@loader_path/'):
                resolved = source.parent / dependency.removeprefix('@loader_path/')
            elif dependency.startswith('@rpath/'):
                suffix = dependency.removeprefix('@rpath/')
                candidates = [source.parent / suffix]
                commands = run('otool', '-l', source).splitlines()
                for index, command in enumerate(commands):
                    if command.strip() == 'cmd LC_RPATH':
                        rpath = commands[index + 2].strip().removeprefix('path ').split(' (offset', 1)[0]
                        rpath = rpath.replace('@loader_path', str(source.parent)).replace('@executable_path', str(source.parent))
                        candidates.append(Path(rpath) / suffix)
                resolved = next((item for item in candidates if item.exists()), None)
                if resolved is None:
                    raise RuntimeError(f'Unresolved dependency {dependency} in {source}')
            elif dependency.startswith('/'):
                resolved = Path(dependency)
            else:
                raise RuntimeError(f'Unresolved dependency {dependency} in {source}')
            if resolved.resolve() == source:
                continue
            bundled = collect(resolved)
            run('install_name_tool', '-change', dependency, '@loader_path/' + bundled.name, target)
        if target.suffix == '.dylib':
            run('install_name_tool', '-id', '@loader_path/' + target.name, target)
        run('codesign', '--force', '--sign', '-', target)
        return target

    for name in ('ffmpeg', 'ffprobe'):
        source = shutil.which(name)
        if not source:
            raise RuntimeError(f'{name} missing from PATH')
        collect(source)
    # Preserve installed Homebrew package licenses for every redistributed library.
    licenses = destination / 'licenses'
    for source in copied:
        if 'Cellar' in source.parts:
            index = source.parts.index('Cellar')
            package_root = Path(*source.parts[:index + 3])
            for candidate in package_root.rglob('*'):
                if candidate.is_file() and candidate.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                    target = licenses / package_root.parent.name / candidate.relative_to(package_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, target)
    return {str(source): target.name for source, target in copied.items()}


def main():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Build on an Apple Silicon Mac')
    python = Path(os.environ.get('NUOMI_BUILD_PYTHON', ROOT.parent / '.venv/bin/python'))
    metadata = json.loads(run(python, '-c',
        'import sys,sysconfig,json; print(json.dumps({"base":sys.base_prefix,"site":sysconfig.get_path("purelib"),"version":f"{sys.version_info.major}.{sys.version_info.minor}"}))'))
    base = Path(metadata['base'])
    if 'uv/python' not in str(base):
        raise RuntimeError('Use a relocatable uv-managed Python, not a system/Homebrew Python')
    if not (ROOT / 'frontend/dist/index.html').exists():
        raise RuntimeError('Build frontend first')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    print('Copying standalone Python and dependencies…', flush=True)
    copy_tree(base, OUT / 'python')
    site = OUT / 'python/lib' / ('python' + metadata['version']) / 'site-packages'
    copy_tree(Path(metadata['site']), site)
    # Editable source links are machine-specific; backend is supplied by PYTHONPATH.
    for pth in site.glob('*.pth'):
        if pth.name.startswith(('_supertale', '__editable__', '_editable_impl_')):
            pth.unlink()
    copy_tree(ROOT / 'src', OUT / 'backend/src')
    copy_tree(ROOT / 'frontend/dist', OUT / 'frontend')
    print('Bundling media tools and their native dependencies…', flush=True)
    libraries = bundle_ffmpeg()
    (OUT / 'build-manifest.json').write_text(json.dumps({
        'platform': 'darwin', 'arch': 'arm64', 'python': metadata['version'],
        'media_libraries': sorted(libraries.values()),
        'revision': run('git', '-C', ROOT, 'rev-parse', 'HEAD'),
    }, indent=2) + '\n')
    print(f'Runtime staged: {OUT}', flush=True)


if __name__ == '__main__':
    main()
