import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_newapi_submission_poll_and_restart_reuses_remote_task(tmp_path, monkeypatch):
    from novelvideo.freezone.video_processing_newapi import execute_newapi
    from novelvideo.generators import video_generator as vg
    calls = []
    poll_count = 0
    class FakeGenerator:
        def __init__(self, **kwargs):
            self.base_url = "https://gateway.example/v1"
            self.api_key = "fake"
        async def _relay_media_input(self, path, **kwargs):
            return "https://relay.example/source.mp4"
        async def _post_json(self, url, payload):
            calls.append(payload)
            return {"id": "provider-1"}
        async def _get_json(self, url):
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                raise RuntimeError("network interrupted")
            return {"status": "completed", "url": "https://result.example/video.mp4"}
        def _extract_video_url(self, task):
            return task["url"]
        async def _download_video(self, url, output):
            Path(output).write_bytes(b"result")
    async def reserve(*args, **kwargs): return "reservation"
    async def confirm(**kwargs): return None
    monkeypatch.setattr(vg, "NewApiVideoGenerator", FakeGenerator)
    monkeypatch.setattr(vg, "_reserve_video_model_call", reserve)
    monkeypatch.setattr(vg, "_confirm_video_model_call", confirm)
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    source.write_bytes(b"input")
    request = {"operation": "extend", "source_duration": 4, "additional_seconds": 3, "prompt": "continue"}
    config = {"model": "extend-model", "output_kind": "continuation", "duration_mode": "additional"}
    with pytest.raises(RuntimeError, match="network"):
        await execute_newapi(source=source, output=output, operation_id="stable-id", request=request, config=config)
    await execute_newapi(source=source, output=output, operation_id="stable-id", request=request, config=config)
    assert len(calls) == 1
    assert calls[0]["idempotency_key"] == "stable-id"
    assert calls[0]["duration"] == 3.0
    assert calls[0]["metadata"]["omni_reference_task_type"] == "extend"
    assert calls[0]["metadata"]["ratio"] == "auto"
    assert output.read_bytes() == b"result"


def test_processing_metadata_is_stage_specific():
    from novelvideo.freezone.video_processing_newapi import build_request
    data = build_request("interpolate-model", "relay", {"operation": "interpolate", "source_duration": 4, "fps": 60, "resolution": "4k"}, {})
    assert data["metadata"]["target_fps"] == 60
    assert data["metadata"]["smart_interpolation"] is True
    assert "omni_reference_task_type" not in data["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 401, 429, 408, 503])
async def test_rejection_refunds_but_unknown_acceptance_does_not(tmp_path, monkeypatch, status):
    from novelvideo.freezone.video_processing_newapi import execute_newapi
    from novelvideo.generators import video_generator as vg
    posts, refunds = [], []
    class FakeGenerator:
        def __init__(self, **kwargs): self.base_url, self.api_key = 'https://gateway/v1', 'fake'
        async def _relay_media_input(self, *args, **kwargs): return 'https://relay/input.mp4'
        async def _post_json(self, *args):
            posts.append(True)
            raise vg.NewApiVideoError('rejected', status_code=status)
    async def reserve(*args, **kwargs): return 'reservation'
    async def refund(reservation_id, **kwargs): refunds.append(reservation_id)
    monkeypatch.setattr(vg, 'NewApiVideoGenerator', FakeGenerator)
    monkeypatch.setattr(vg, '_reserve_video_model_call', reserve)
    monkeypatch.setattr(vg, '_refund_video_model_call', refund)
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'input')
    kwargs = dict(source=source, output=tmp_path / 'out.mp4', operation_id='stable',
                  request={'operation': 'extend', 'source_duration': 4, 'additional_seconds': 3}, config={'model': 'extend'})
    with pytest.raises(vg.NewApiVideoError):
        await execute_newapi(**kwargs)
    with pytest.raises(RuntimeError):
        await execute_newapi(**kwargs)
    assert len(posts) == 1
    assert refunds == (['reservation', 'reservation'] if status not in {408, 503} else [])


@pytest.mark.asyncio
async def test_failed_receipt_retries_idempotent_refund_after_crash(tmp_path, monkeypatch):
    from novelvideo.freezone.video_processing_newapi import execute_newapi
    from novelvideo.generators import video_generator as vg
    from types import SimpleNamespace
    refunds = []
    async def refund(reservation_id, **kwargs): refunds.append(reservation_id)
    monkeypatch.setattr(vg, 'NewApiVideoGenerator', lambda **kwargs: SimpleNamespace(api_key='fake', base_url='https://gateway'))
    monkeypatch.setattr(vg, '_refund_video_model_call', refund)
    output = tmp_path / 'out.mp4'
    output.with_suffix('.provider.json').write_text(json.dumps({'reservation_id': 'reserved', 'task_id': 'remote', 'failed': 'provider cancelled'}))
    with pytest.raises(RuntimeError, match='provider cancelled'):
        await execute_newapi(source=tmp_path / 'source.mp4', output=output, operation_id='stable', request={}, config={'model': 'model'})
    assert refunds == ['reserved']


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_confirmed', [False, True])
async def test_cancellation_refunds_only_after_provider_confirmation(tmp_path, monkeypatch, cancel_confirmed):
    import asyncio
    from novelvideo.freezone.video_processing_newapi import execute_newapi
    from novelvideo.generators import video_generator as vg
    refunds = []
    class FakeGenerator:
        def __init__(self, **kwargs): self.api_key, self.base_url = 'fake', 'https://gateway'
        async def _get_json(self, *args): raise asyncio.CancelledError()
        async def _cancel_provider_task(self, task_id):
            assert task_id == 'remote'
            return cancel_confirmed
    async def refund(reservation_id, **kwargs): refunds.append(reservation_id)
    monkeypatch.setattr(vg, 'NewApiVideoGenerator', FakeGenerator)
    monkeypatch.setattr(vg, '_refund_video_model_call', refund)
    output = tmp_path / 'out.mp4'
    receipt = output.with_suffix('.provider.json')
    receipt.write_text(json.dumps({'reservation_id': 'reserved', 'task_id': 'remote'}))
    with pytest.raises(asyncio.CancelledError):
        await execute_newapi(source=tmp_path / 'source.mp4', output=output, operation_id='stable', request={}, config={'model': 'model'})
    assert refunds == (['reserved'] if cancel_confirmed else [])
    assert bool(json.loads(receipt.read_text()).get('failed')) == cancel_confirmed


@pytest.mark.asyncio
async def test_ambiguous_submission_never_posts_again(tmp_path, monkeypatch):
    from novelvideo.freezone.video_processing_newapi import execute_newapi
    from novelvideo.generators import video_generator as vg
    calls = []
    class FakeGenerator:
        def __init__(self, **kwargs):
            self.base_url, self.api_key = "https://gateway.example/v1", "fake"
        async def _relay_media_input(self, *args, **kwargs): return "https://relay/source.mp4"
        async def _post_json(self, url, body):
            calls.append(body)
            raise TimeoutError("lost acceptance response")
    async def reserve(*args, **kwargs): return "reservation"
    monkeypatch.setattr(vg, "NewApiVideoGenerator", FakeGenerator)
    monkeypatch.setattr(vg, "_reserve_video_model_call", reserve)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"input")
    kwargs = dict(source=source, output=tmp_path / "out.mp4", operation_id="stable", request={"operation": "extend", "source_duration": 4, "additional_seconds": 3}, config={"model": "extend"})
    with pytest.raises(TimeoutError):
        await execute_newapi(**kwargs)
    with pytest.raises(RuntimeError, match="禁止自动重复提交"):
        await execute_newapi(**kwargs)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_real_newapi_http_transport_payload_poll_and_download(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.freezone.video_processing_newapi import execute_newapi
    from novelvideo.generators import video_generator as vg
    from novelvideo import config
    calls = []
    class Response:
        status = 200
        headers = {}
        def __init__(self, data): self.data = data
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def text(self): return json.dumps(self.data)
        async def read(self): return b"downloaded-video"
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return Response({"id": "remote-42"})
        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return Response({"status": "completed", "url": "https://cdn.example/result.mp4"})
    async def reserve(*args, **kwargs): return "reservation"
    async def confirm(**kwargs): pass
    async def relay(*args, **kwargs): return "https://relay.example/input.mp4"
    monkeypatch.setattr(config, "get_effective_newapi_gateway_config", lambda: SimpleNamespace(api_key="test-key", base_url="https://gateway.example/v1"))
    monkeypatch.setattr(vg.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(vg, "_reserve_video_model_call", reserve)
    monkeypatch.setattr(vg, "_confirm_video_model_call", confirm)
    monkeypatch.setattr(vg.NewApiVideoGenerator, "_relay_media_input", relay)
    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "output.mp4"
    await execute_newapi(source=source, output=output, operation_id="fps-independent", request={"operation": "interpolate", "source_duration": 42, "fps": 60, "resolution": "4k"}, config={"model": "video-frame-rate"})
    assert calls[0][0:2] == ("POST", "https://gateway.example/v1/video/generations")
    assert calls[0][2]["headers"]["Idempotency-Key"] == "fps-independent"
    assert calls[0][2]["json"] == {"model": "video-frame-rate", "prompt": "", "duration": 42.0,
        "metadata": {"reference_videos": ["https://relay.example/input.mp4"], "ratio": "auto", "target_fps": 60, "smart_interpolation": True, "resolution_tier": "4k"}}
    assert calls[1][1] == "https://gateway.example/v1/videos/remote-42"
    assert calls[2][1] == "https://cdn.example/result.mp4"
    assert "headers" not in calls[2][2]  # Never forward gateway credentials to CDN.
    assert output.read_bytes() == b"downloaded-video"
