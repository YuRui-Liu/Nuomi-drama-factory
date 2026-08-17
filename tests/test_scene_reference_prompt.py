import pytest
import httpx

from novelvideo.generators.scene_reference_images import build_scene_reference_prompt
from novelvideo.models import NovelScene


@pytest.mark.asyncio
async def test_grsai_poll_retries_transient_read_timeout(monkeypatch):
    from novelvideo.generators import scene_reference_images
    from novelvideo.media_capabilities.image.grsai import GrsaiSnapshot

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def query(self, task_id, *, api_key):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadTimeout("", request=httpx.Request("GET", "https://grsai.test"))
            return GrsaiSnapshot(
                id=task_id,
                status="succeeded",
                results=[{"url": "https://files.test/image.png"}],
            )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(scene_reference_images.asyncio, "sleep", no_sleep)
    client = FakeClient()

    snapshot = await scene_reference_images._poll_grsai_image_result(
        client,
        "task-1",
        api_key="secret",
        timeout_seconds=30,
    )

    assert snapshot.status == "succeeded"
    assert client.calls == 2


def test_scene_reference_prompt_combines_base_prompt_for_variant_without_base_image():
    base_scene = NovelScene(
        name="卫生间",
        scene_type="interior",
        environment_prompt="白瓷砖墙面，正面是洗手台。",
    )
    variant_scene = NovelScene(
        name="卫生间_漏水",
        scene_type="interior",
        base_scene_id="卫生间",
        variant_id="漏水",
        variant_prompt="地面积水，天花板持续滴水。",
        environment_prompt="",
    )

    prompt = build_scene_reference_prompt(
        "master",
        variant_scene,
        base_scene=base_scene,
    )

    assert "白瓷砖墙面" in prompt
    assert "地面积水" in prompt


def test_scene_reference_prompt_keeps_variant_delta_out_of_scene_description():
    base_scene = NovelScene(
        name="城市街道",
        scene_type="exterior",
        environment_prompt="正面：深灰色双向车道。左侧：现代商业立面。",
    )
    variant_scene = NovelScene(
        name="城市街道_雨夜版",
        scene_type="exterior",
        base_scene_id="城市街道",
        variant_id="雨夜版",
        variant_prompt="下着小雨，地面湿润有积水，反射微弱路灯光。",
        environment_prompt="",
    )

    prompt = build_scene_reference_prompt(
        "master",
        variant_scene,
        base_scene=base_scene,
    )

    assert "VARIANT DELTA PROMPT:\n下着小雨" in prompt
    scene_description = prompt.split("SCENE DESCRIPTION:", 1)[1].split(
        "PROJECT STYLE PRESET:", 1
    )[0]
    assert "正面：深灰色双向车道" in scene_description
    assert "下着小雨" not in scene_description
    assert "地面湿润有积水" not in scene_description


async def test_scene_reference_newapi_uses_normalized_gateway_base_url(monkeypatch, tmp_path):
    from novelvideo.generators import scene_reference_images

    captured: dict[str, str | None] = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured["base_url"] = kwargs.get("base_url")
        return b"image-bytes", "", ""

    monkeypatch.setattr(
        scene_reference_images,
        "NEWAPI_BASE_URL",
        "https://relayclaw.cdnfg.com/",
        raising=False,
    )
    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    await scene_reference_images.generate_scene_reference_image(
        project_dir=tmp_path,
        scene=NovelScene(name="Hall", environment_prompt="wide hall"),
        kind="master",
    )

    assert captured["base_url"] == "https://relayclaw.cdnfg.com/v1"


@pytest.mark.asyncio
async def test_scene_reference_grsai_uses_persisted_runtime_not_newapi(monkeypatch, tmp_path):
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    calls = []

    async def fake_grsai(**kwargs):
        calls.append(kwargs)
        return b"png-bytes", "", ""

    async def fail_newapi(**_kwargs):
        raise AssertionError("scene generation must not call DramaClawAPI")

    monkeypatch.setattr(scene_reference_images, "_call_grsai_image_api", fake_grsai)
    monkeypatch.setattr(scene_reference_images, "_call_newapi_image_api", fail_newapi)

    output = await scene_reference_images.generate_scene_reference_image(
        project_dir=tmp_path,
        scene=NovelScene(name="大厅", description="地下大厅"),
        kind="master",
        provider="grsai",
        model="gpt-image-2",
    )

    assert output.read_bytes() == b"png-bytes"
    assert calls[0]["model"] == "gpt-image-2"
