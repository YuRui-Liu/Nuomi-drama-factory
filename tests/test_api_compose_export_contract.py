from io import BytesIO
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


pytestmark = pytest.mark.m09


class _FakeStore:
    async def get_beats_as_dicts(self, episode: int):
        assert episode == 3
        return [{"beat_number": 1, "narration_segment": "Hello"}]


def _client(monkeypatch, tmp_path) -> TestClient:
    from novelvideo.api.routes import generation
    from novelvideo.api.deps import ProjectResolution

    async def fake_make_sqlite_store(username, project):
        assert username == "alice"
        assert project == "demo"
        return _FakeStore()

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    app = FastAPI()
    app.include_router(generation.router)
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "alice"}
    monkeypatch.setattr(generation, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(generation, "make_sqlite_store", fake_make_sqlite_store)
    return TestClient(app)


def _write_export_assets(project_dir, episode: int = 3) -> None:
    ep_tag = f"ep{episode:03d}"
    (project_dir / "audio" / ep_tag).mkdir(parents=True)
    (project_dir / "audio" / ep_tag / "beat_01.mp3").write_bytes(b"mp3")
    (project_dir / "videos" / "beats" / ep_tag).mkdir(parents=True)
    (project_dir / "videos" / "beats" / ep_tag / "beat_01.mp4").write_bytes(b"beat")
    (project_dir / "videos" / "episodes").mkdir(parents=True)
    (project_dir / "videos" / "episodes" / f"{ep_tag}_final.mp4").write_bytes(b"final")


def test_export_video_returns_final_video_file(monkeypatch, tmp_path):
    _write_export_assets(tmp_path)
    response = _client(monkeypatch, tmp_path).get(
        "/projects/demo/episodes/3/export/video",
    )

    assert response.status_code == 200
    assert response.content == b"final"
    assert "attachment" in response.headers["content-disposition"]
    assert "ep003_final.mp4" in response.headers["content-disposition"]


def test_export_zip_contains_beat_media_final_video_and_srt(monkeypatch, tmp_path):
    _write_export_assets(tmp_path)
    response = _client(monkeypatch, tmp_path).post(
        "/projects/demo/episodes/3/export/zip",
    )

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        names = set(zf.namelist())

    assert "audio/beat_01.mp3" in names
    assert "video/beat_01.mp4" in names
    assert "ep003_final.mp4" in names
    assert "ep003.srt" in names


def test_prepare_zip_returns_retryable_native_attachment(monkeypatch, tmp_path):
    _write_export_assets(tmp_path)
    client = _client(monkeypatch, tmp_path)
    response = client.post('/projects/demo/episodes/3/export/zip?prepare_only=true')
    assert response.headers['content-type'].startswith('application/json')
    path = response.json()['download_url']
    assert path == '/api/v1/projects/demo/episodes/3/export/zip'
    for _ in range(2):
        download = client.get(path.removeprefix('/api/v1'))
        assert download.status_code == 200
        assert download.headers['content-type'].startswith('application/zip')
        assert 'attachment' in download.headers['content-disposition']
        with zipfile.ZipFile(BytesIO(download.content)) as archive:
            assert 'ep003.srt' in archive.namelist()


@pytest.mark.asyncio
async def test_zip_failure_preserves_previous_archive(monkeypatch, tmp_path):
    from novelvideo.export.episode_export import build_episode_zip_file
    _write_export_assets(tmp_path)
    old = tmp_path / 'videos/episodes/demo_第3集.zip'
    old.write_bytes(b'previous complete archive')
    def fail(*args, **kwargs): raise OSError('disk unavailable')
    monkeypatch.setattr(zipfile.ZipFile, 'write', fail)
    with pytest.raises(OSError):
        await build_episode_zip_file(tmp_path, 'demo', 3, [{'beat_number': 1, 'narration_segment': 'Hello'}])
    assert old.read_bytes() == b'previous complete archive'


def test_srt_export_falls_back_when_audio_duration_probe_fails(monkeypatch, tmp_path):
    from novelvideo.export import episode_export

    _write_export_assets(tmp_path)

    async def fail_duration(_audio_path):
        raise RuntimeError("ffprobe missing")

    monkeypatch.setattr(episode_export, "get_audio_duration_async", fail_duration)
    response = _client(monkeypatch, tmp_path).get(
        "/projects/demo/episodes/3/export/srt",
    )

    assert response.status_code == 200
    assert b"00:00:00,000 --> 00:00:05,000" in response.content


def test_srt_export_accepts_director_episode_without_legacy_beats(monkeypatch, tmp_path):
    from novelvideo.export import episode_export

    async def empty_beats(self, episode):
        return []

    async def director_srt(project_dir, episode, beats):
        assert beats == []
        return "1\n00:00:00,000 --> 00:00:08,000\n阿岑：先进山。\n"

    monkeypatch.setattr(_FakeStore, "get_beats_as_dicts", empty_beats)
    monkeypatch.setattr(episode_export, "build_srt_content", director_srt)
    response = _client(monkeypatch, tmp_path).get("/projects/demo/episodes/3/export/srt")
    assert response.headers["content-type"].startswith("text/srt")
    assert "阿岑" in response.text


def test_srt_export_incomplete_director_returns_error_status(monkeypatch, tmp_path):
    from novelvideo.export import episode_export

    async def incomplete(*args):
        raise ValueError("DIRECTOR_COMPOSITION_INCOMPLETE: 第二组视频未完成")

    monkeypatch.setattr(episode_export, "build_srt_content", incomplete)
    response = _client(monkeypatch, tmp_path).get("/projects/demo/episodes/3/export/srt")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_srt_file_accepts_director_episode_without_legacy_beats(monkeypatch, tmp_path):
    from novelvideo.export import episode_export

    async def director_srt(*args):
        return "1\n00:00:00,000 --> 00:00:08,000\n你好\n"

    monkeypatch.setattr(episode_export, "build_srt_content", director_srt)
    result = await episode_export.build_episode_srt_file(tmp_path, 3, [])
    assert result is not None
    assert "你好" in result.read_text()
