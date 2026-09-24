from __future__ import annotations

import json

import pytest

from novelvideo.text_task_runtime import models_catalog


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path):
    # 把 cwd 与 HOME 重定向到临时目录，避免污染本机 ~/.codebuddy/models.json。
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(models_catalog.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("NUOMI_PROJECT_ROOT", raising=False)
    yield


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_falls_back_to_builtin_when_no_models_json():
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["workbuddy"] == [
        "Hy4 preview",
        "Hy3",
        "Deepseek-V4",
        "Deepseek-V4.1-Flash",
        "default-model",
    ]
    assert catalog["codex"] == ["gpt-5.6-sol", "gpt-6-astra"]
    assert catalog["model_api"] == []


def test_user_level_available_models_is_used(tmp_path):
    _write(
        tmp_path / ".codebuddy" / "models.json",
        {
            "availableModels": ["custom-a", "custom-b"],
            "models": [{"id": "ignored"}],
        },
    )
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["workbuddy"] == ["custom-a", "custom-b"]


def test_project_level_takes_precedence_over_user_level(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write(
        project_root / ".codebuddy" / "models.json",
        {"availableModels": ["proj-only"]},
    )
    _write(
        tmp_path / ".codebuddy" / "models.json",
        {"availableModels": ["user-level"]},
    )
    # 用 NUOMI_PROJECT_ROOT 指向 proj，让候选路径里出现项目级。
    import os
    os.environ["NUOMI_PROJECT_ROOT"] = str(project_root)
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["workbuddy"][0] == "proj-only"


def test_models_id_fallback_when_available_models_absent(tmp_path):
    _write(
        tmp_path / ".codebuddy" / "models.json",
        {
            "models": [
                {"id": "alpha"},
                {"id": "beta"},
                {"name": "no-id-entry"},
            ]
        },
    )
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["workbuddy"] == ["alpha", "beta"]


def test_env_var_overrides_builtin_default(monkeypatch):
    monkeypatch.setenv("TEXT_TASK_DEFAULT_MODELS_CODEX", "alpha, beta, alpha")
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["codex"] == ["alpha", "beta"]


def test_malformed_models_json_is_ignored(tmp_path):
    (tmp_path / ".codebuddy").mkdir()
    (tmp_path / ".codebuddy" / "models.json").write_text("{not json", encoding="utf-8")
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["workbuddy"] == [
        "Hy4 preview",
        "Hy3",
        "Deepseek-V4",
        "Deepseek-V4.1-Flash",
        "default-model",
    ]


def test_model_api_runtime_does_not_consult_models_json(tmp_path):
    _write(tmp_path / ".codebuddy" / "models.json", {"availableModels": ["ignored"]})
    catalog = models_catalog.get_runtime_model_catalog()
    assert catalog["model_api"] == []