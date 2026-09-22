import builtins
from pathlib import Path
import subprocess
import sys

import portalocker

from novelvideo.production_workflow import store


def test_workflow_store_import_does_not_require_unix_fcntl(monkeypatch):
    original_import = builtins.__import__

    def windows_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ModuleNotFoundError("No module named 'fcntl'")
        return original_import(name, *args, **kwargs)

    # portalocker is preloaded using this host's OS backend; the application
    # module itself must import without a Unix-only dependency on Windows.
    monkeypatch.setattr(builtins, "__import__", windows_import)
    source = Path(store.__file__).read_text()
    exec(compile(source, store.__file__, "exec"), {
        "__name__": "novelvideo.production_workflow._windows_import_probe",
        "__package__": "novelvideo.production_workflow",
    })


def test_project_lock_is_reentrant_and_exception_safe(tmp_path):
    code = """
import sys
from novelvideo.production_workflow.store import production_workflow_project_lock
with production_workflow_project_lock(sys.argv[1]):
    try:
        with production_workflow_project_lock(sys.argv[1]):
            raise ValueError('test rollback')
    except ValueError:
        pass
    with production_workflow_project_lock(sys.argv[1]):
        pass
with production_workflow_project_lock(sys.argv[1]):
    pass
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()


def test_project_lock_excludes_other_process_until_release(tmp_path):
    code = """
import sys, portalocker
try:
    with portalocker.Lock(sys.argv[1], mode='a+b', timeout=0):
        pass
except portalocker.exceptions.LockException:
    sys.exit(23)
"""
    lock_path = tmp_path / ".production_workflow.lock"
    with store.production_workflow_project_lock(tmp_path):
        locked = subprocess.run([sys.executable, "-c", code, str(lock_path)], timeout=5)
        assert locked.returncode == 23
    released = subprocess.run([sys.executable, "-c", code, str(lock_path)], timeout=5)
    assert released.returncode == 0
