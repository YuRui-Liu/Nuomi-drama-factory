def test_scene_360_provider_defaults_to_grsai_when_env_is_empty(monkeypatch):
    from novelvideo import stage_asset_tasks

    monkeypatch.setenv("SCENE_360_IMAGE_PROVIDER", "")
    monkeypatch.setenv("SCENE_360_PROVIDER", "")
    monkeypatch.setenv("NANOBANANA_PROVIDER", "")

    assert stage_asset_tasks.resolve_scene_360_image_provider() == "grsai"


def test_scene_360_provider_ignores_legacy_explicit_newapi(monkeypatch):
    from novelvideo import stage_asset_tasks

    monkeypatch.setenv("SCENE_360_IMAGE_PROVIDER", "newapi")

    assert stage_asset_tasks.resolve_scene_360_image_provider("newapi") == "grsai"


def test_scene_360_model_ignores_legacy_explicit_newapi_model(monkeypatch):
    from novelvideo import stage_asset_tasks

    monkeypatch.setattr(
        stage_asset_tasks, "_load_grsai_scene_360_model", lambda: "gpt-image-2"
    )

    assert (
        stage_asset_tasks.resolve_scene_360_image_model("newapi", "newapi_gpt_image2")
        == "gpt-image-2"
    )
