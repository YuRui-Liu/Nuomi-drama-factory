"""AI endpoint suggestions; never silently replace an editor's selection."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator


class FrameRecommendation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    mode: Literal['first', 'first_last']
    first_frame_id: str
    last_frame_id: str | None
    reason: str = Field(min_length=1)


class FrameRecommendations(BaseModel):
    model_config = ConfigDict(extra='forbid')
    segments: list[FrameRecommendation]


def validate_recommendations(result, frames, segments):
    cells = {frame['id']: frame for frame in frames}
    if [r.id for r in result.segments] != [s['id'] for s in segments]:
        raise ValueError('recommendation must preserve all segment IDs and order')
    pairs = []
    for suggestion, source in zip(result.segments, segments, strict=True):
        first, last = suggestion.first_frame_id, suggestion.last_frame_id
        if first not in cells:
            raise ValueError('recommended first frame does not exist')
        if source['id'] in cells and first != source['id']:
            raise ValueError('each shot must use its own storyboard cell, not another shot')
        if suggestion.mode == 'first':
            if last:
                raise ValueError('first-only recommendation cannot include a tail')
        else:
            if last not in cells or first == last:
                raise ValueError('recommended endpoints must be distinct existing cells')
            for key, anchor in ((first, 'start'), (last, 'end')):
                panel = cells[key].get('static_panel')
                if panel and panel.get('anchor') != anchor:
                    raise ValueError('first/last recommendation requires designed start/end states')
                if panel and panel.get('shot_id') and panel['shot_id'] != source['id']:
                    raise ValueError('recommended endpoints must belong to the same director shot')
        pairs.append({**source, 'mode': suggestion.mode, 'first_frame_id': first, 'last_frame_id': last or '',
                      'selection_source': 'ai', 'recommendation': suggestion.model_dump()})
    return pairs


async def recommend_frames(runtime, frames, segments, *, images=None):
    frame_ids = Literal[tuple(frame['id'] for frame in frames)]
    segment_ids = Literal[tuple(segment['id'] for segment in segments)]
    scoped_item = create_model('AvailableFrameRecommendation', __base__=FrameRecommendation,
        id=(segment_ids, ...), first_frame_id=(frame_ids, ...), last_frame_id=(frame_ids | None, ...))

    @model_validator(mode='after')
    def validate_source(self):
        validate_recommendations(self, frames, segments)
        return self

    response_type = create_model('AvailableFrameRecommendations', __base__=FrameRecommendations,
        segments=(list[scoped_item], ...), __validators__={'validate_source': validate_source})
    result = await runtime.run_structured(output_type=response_type,
        system_prompt=('你是分镜导演，为每个既定视频片段推荐首帧或首尾帧并用中文解释理由。'
            '只推荐，不改写剧情、对白、时长或片段结构。每个已设计镜头对应独立片段，不能将四个镜头压成整组一对首尾帧。默认优先首帧，让后续动作自然发展；'
            '只有结束动作、站位或构图需要锁定，且有对应同段起止画格，才推荐首尾帧。'
            '不能将相邻镜头或关键瞬间机械凑成首尾。static_panel 存在时，首尾模式要求 anchor 分别为 start/end；'
            '首帧模式允许选用合适的已有画格作为起点，last_frame_id 必须为 null。'
            '逐一返回输入片段 ID，顺序不变，只能选择真实画格 ID。片段 ID 与画格 ID 相同时必须选择自身画格作为首帧。整张分镜不作为角色 Ref。'),
        prompt=json.dumps({'frames': frames, 'segments': segments}, ensure_ascii=False), images=images)
    return validate_recommendations(FrameRecommendations.model_validate(result), frames, segments)
