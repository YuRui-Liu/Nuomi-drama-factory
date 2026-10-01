import json
import pytest
from types import SimpleNamespace

from novelvideo.director_plan.models import DirectorShotIntent
from novelvideo.task_backend.runners import narrative_group_video as runner
from novelvideo.media_capabilities.video import h3_prompt_optimizer as optimizer
from novelvideo.media_capabilities.video import h3_episode_pack as pack
from novelvideo.media_capabilities.video.models import H3Mode


def _intent(effect="先松口气，再意识到判断尚未证实"):
    return DirectorShotIntent(narrative_purpose="呈现人物的偏信而非事实", audience_attention="被追问后的停顿",
                              emotional_effect=effect, continuity_strategy="延续广播尾音，不新增声音线索")


def _segment():
    return runner.H3DirectorSegment(segment_id="shot-1", beat_number=1, prompt="站定听广播",
                                   duration_seconds=4, first_frame="frame.png", speaker="岑砚", dialogue="像她的暗号。")


def _context(monkeypatch, intent):
    monkeypatch.setattr(runner, "_snapshot_frame_sha256", lambda *args: "a" * 64)
    return runner._prompt_context(_segment(), {"id": "shot-1", "director_intent": intent.model_dump()}, None, None)


def _episode(context):
    entry = pack.H3EpisodeVideoSegment(segment_id="shot-1", group_id="g1", shot_ids=("shot-1",),
        duration_seconds=4, style_snapshot_id="style", source_segment=_segment(), context=context,
        mode=H3Mode.I2VA, summary="听广播")
    return pack.H3EpisodeInput(episode=1, director_revision_id="r1", style_hash="style", style_video={}, segments=(entry,))


def test_projected_intent_reaches_both_optimizer_requests_and_changes_cache(monkeypatch):
    context = _context(monkeypatch, _intent())
    changed = _context(monkeypatch, _intent("怀疑转为暂时信任"))
    task = optimizer._build_task(_segment(), context, H3Mode.I2VA)
    assert "呈现人物的偏信而非事实" in task
    assert "Verbatim dialogue: 像她的暗号。" in task
    assert "OS/internal monologue and broadcast" in task
    assert optimizer._input_hash(_segment(), context, H3Mode.I2VA) != optimizer._input_hash(_segment(), changed, H3Mode.I2VA)
    episode, revised = _episode(context), _episode(changed)
    payload = pack._prompt_segment(episode, 0, episode.segments[0])
    assert "呈现人物的偏信而非事实" in payload["director_context"]
    assert payload["dialogue"] == "像她的暗号。"
    assert "OS/internal monologue and broadcast" in pack._episode_task(episode)
    assert pack._segment_input_hash(episode, episode.segments[0]) != pack._segment_input_hash(revised, revised.segments[0])


def test_active_shot_intent_reaches_source_state_context_without_changing_source():
    shot = SimpleNamespace(id="shot-1", visible_start_state="静听", visible_end_state="停顿", source_span_ids=("s1",), intent=_intent())
    active = SimpleNamespace(groups=[SimpleNamespace(shots=[shot])])
    source = {"s1": {"text": "像她的暗号。", "type": "dialogue"}}
    result = json.loads(runner._source_state_context(active, _segment(), "", source_blocks=source))
    state = result["source_shot_states"][0]
    assert state["director_intent"] == _intent().model_dump()
    assert state["screenplay_source"] == [{"source_id": "s1", **source["s1"]}]
    shot.intent = None
    assert json.loads(runner._source_state_context(active, _segment(), ""))["source_shot_states"][0]["director_intent"] is None


def test_pair_keeps_ordered_intents_and_legacy_has_no_invented_intent(monkeypatch):
    monkeypatch.setattr(runner, "_snapshot_frame_sha256", lambda *args: "a" * 64)
    pair = runner._synthetic_pair_beat({"id": "shot-1", "director_intent": _intent().model_dump()},
                                     {"id": "shot-2", "director_intent": _intent("担忧").model_dump()})
    context = runner._prompt_context(_segment(), pair, None, None)
    intents = json.loads(context.director_context)["source_director_intents"]
    assert [item["shot_id"] for item in intents] == ["shot-1", "shot-2"]
    assert intents[1]["director_intent"]["emotional_effect"] == "担忧"
    assert runner._prompt_context(_segment(), {"id": "old"}, None, None).director_context == ""


@pytest.mark.asyncio
async def test_optimizer_compiles_performance_and_reuses_only_matching_intent(tmp_path):
    from tests.media_capabilities.video.test_h3_prompt_optimizer import _director_plan, _segment, _context

    reaction = "He pauses briefly, holds his breath, then exhales; his shoulders settle."
    plan = _director_plan()
    shot = plan.shots[0]
    actions = tuple(action.model_copy(update={"description": reaction}) if action.phase == "settle" else action
                    for action in shot.actions)
    plan = plan.model_copy(update={"shots": (shot.model_copy(update={"actions": actions}),)})
    calls = []

    class Agent:
        async def run(self, task):
            calls.append(task)
            return SimpleNamespace(output=plan)

    service = optimizer.H3PromptOptimizer(Agent(), tmp_path)
    context = _context().model_copy(update={"director_context": json.dumps({"director_intent": _intent().model_dump()}, ensure_ascii=False)})
    first = await service.optimize_segment(_segment(), context, H3Mode.I2VA)
    second = await service.optimize_segment(_segment(), context, H3Mode.I2VA)
    changed = context.model_copy(update={"director_context": json.dumps({"director_intent": _intent("怀疑").model_dump()}, ensure_ascii=False)})
    third = await service.optimize_segment(_segment(), changed, H3Mode.I2VA)
    assert len(calls) == 2
    assert second.cache_hit and not third.cache_hit
    assert "呈现人物的偏信而非事实" in calls[0]
    assert reaction in first.prompt
    assert "<d>[Chinese]别过来</d>" in first.prompt
    assert "呈现人物的偏信而非事实" not in first.prompt
