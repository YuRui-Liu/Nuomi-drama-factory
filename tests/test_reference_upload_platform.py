"""Unsupported filesystem primitives must not prevent API imports."""

import importlib.util
import os
from pathlib import Path
import sys

import pytest


def test_reference_upload_import_without_directory_flag(monkeypatch):
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    path = Path(__file__).parents[1] / "src/novelvideo/narrative_groups/reference_uploads.py"
    spec = importlib.util.spec_from_file_location("reference_upload_platform_check", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    def unexpected_open(*args, **kwargs):
        pytest.fail("Unsupported platform must reject before filesystem access")

    monkeypatch.setattr(os, "open", unexpected_open)
    with pytest.raises(module.InvalidReferenceUpload, match="unsupported"):
        module._open_dir_chain(Path.cwd(), (), create=False)
