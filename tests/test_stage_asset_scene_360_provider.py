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


def test_text_only_scene_360_prompt_does_not_require_missing_reference_images():
    from novelvideo.director_world.scene_360_builder import build_prompt

    prompt = build_prompt(
        scene_name="广播站",
        scene_description="""场景名称：广播站
master 图代表正面半区并提供视觉风格锚点。
reverse 图应代表背面半区。
如果方向缺失，请基于场景类型和 master 视觉风格补全。
正面是广播控制台，背面是金属门。""",
        style="post_apocalyptic",
        has_master=False,
        has_reverse=False,
        has_spatial_layout=False,
    )

    assert "No image reference is attached." in prompt
    assert "Scene description is the sole geometry and scene-identity source." in prompt
    assert "scene description and master visual reference" not in prompt
    assert "Master image ground-truths" not in prompt
    assert "master 图代表" not in prompt
    assert "reverse 图应代表" not in prompt
    assert "master 视觉风格" not in prompt
