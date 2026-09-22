import subprocess
import sys

from novelvideo.utils import state_index_files


def test_index_lock_excludes_other_process_without_fcntl(tmp_path, monkeypatch):
    # Exercise the old Windows fallback on any host.
    monkeypatch.setattr(state_index_files, "fcntl", None, raising=False)
    index_path = tmp_path / "pool.json"
    code = """
import sys, portalocker
try:
    with portalocker.Lock(sys.argv[1], mode='a+b', timeout=0):
        pass
except portalocker.exceptions.LockException:
    sys.exit(23)
"""
    command = [sys.executable, "-c", code, str(index_path.with_suffix(".json.lock"))]
    with state_index_files.index_file_lock(index_path):
        assert subprocess.run(command, timeout=5).returncode == 23
    assert subprocess.run(command, timeout=5).returncode == 0
