import json
from types import SimpleNamespace

from novelvideo.media_capabilities.video.h3_episode_pack import _prompt_segment
from tests.media_capabilities.video.test_h3_episode_pack import _input


def test_episode_prompt_preserves_director_constraints():
    value = _input()
    entry = value.segments[0]
    context = entry.context.model_copy(update={"director_context": '{"visible_end_state":"下方留白"}'})
    entry = entry.model_copy(update={"context": context})
    assert _prompt_segment(value, 0, entry)["director_context"] == context.director_context


def test_source_states_are_ordered_by_segment_and_keep_blank_line_verbatim():
    from novelvideo.task_backend.runners.narrative_group_video import _source_state_context
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    def shot(shot_id, start, end):
        return SimpleNamespace(id=shot_id, visible_start_state=start, visible_end_state=end)

    plan = SimpleNamespace(groups=[SimpleNamespace(shots=[
        shot("s1", "本子打开", "写有东侧闸门，下方留白，笔尖停住"),
        shot("s2", "持本", "合本"), shot("other", "无关", "无关"),
    ])])
    segment = H3DirectorSegment(segment_id="segment", beat_number=1, prompt="记录",
                                duration_seconds=5, first_frame="frame.png",
                                source_shot_ids=("s2", "s1"))
    result = json.loads(_source_state_context(plan, segment, '{"camera":"static"}'))
    assert result["director_context"] == {"camera": "static"}
    assert result["source_shot_states"] == [
        {"shot_id": "s2", "visible_start_state": "持本", "visible_end_state": "合本"},
        {"shot_id": "s1", "visible_start_state": "本子打开",
         "visible_end_state": "写有东侧闸门，下方留白，笔尖停住"},
    ]


def test_video_context_keeps_exact_script_action_separate_from_director_summary():
    from novelvideo.task_backend.runners.narrative_group_video import _source_state_context
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    shot = SimpleNamespace(id="s1", visible_start_state="持本", visible_end_state="留白",
                           source_span_ids=("line-116",))
    plan = SimpleNamespace(groups=[SimpleNamespace(shots=[shot])])
    segment = H3DirectorSegment(segment_id="s1", beat_number=1, prompt="unfinished line",
                               duration_seconds=5, first_frame="frame.png", source_shot_ids=("s1",))
    source = {"line-116": {"kind": "action", "text": "写下东侧闸门，下方留白。笔尖停住，没有落字。"},
              "other": {"kind": "action", "text": "不得混入的邻镜动作"}}
    result = json.loads(_source_state_context(plan, segment, "{}", source_blocks=source))
    assert result["source_shot_states"][0]["screenplay_source"] == [
        {"source_id": "line-116", **source["line-116"]}]
    assert "邻镜动作" not in json.dumps(result, ensure_ascii=False)
