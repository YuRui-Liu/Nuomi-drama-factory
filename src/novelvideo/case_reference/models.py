from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novelvideo.freezone.video_director.models import DirectorDraft

Dimension = Literal['camera', 'rhythm', 'performance', 'ending']


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=128)
    canvas_id: str = Field(min_length=1, max_length=128)
    node_id: str = Field(min_length=1, max_length=128)
    segment_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(pattern=r'^h3-[0-9a-f]{16}$')
    draft: DirectorDraft
    effective_draft: DirectorDraft
    prompt_hashes: list[str] = Field(default_factory=list, max_length=10)
    dimensions: list[Dimension] = Field(min_length=1, max_length=4)
    description: str = Field(default='', max_length=10000)

    @model_validator(mode='after')
    def validate_target(self):
        self.dimensions = list(dict.fromkeys(self.dimensions))
        self.prompt_hashes = list(dict.fromkeys(self.prompt_hashes))
        original = next((s for s in self.draft.segments if s.id == self.segment_id), None)
        effective = next((s for s in self.effective_draft.segments if s.id == self.segment_id), None)
        if original is None or effective is None or not original.prompt.strip():
            raise ValueError('目标分段不存在或缺少剧情提示词')
        if len(original.prompt) > 20000:
            raise ValueError('目标提示词过长')
        # Connected inputs may replace frames/references, but never story or settings.
        exclude = {'references': True, 'segments': {'__all__': {'first_frame', 'last_frame'}}}
        if self.draft.model_dump(exclude=exclude) != self.effective_draft.model_dump(exclude=exclude):
            raise ValueError('目标剧情、模型设置或草稿版本不一致')
        return self


class Suggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dimension: Dimension
    suggestion: str = Field(min_length=1, max_length=4000)
    basis: str = Field(min_length=1, max_length=4000)


class ProposalResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=20000)
    suggestions: list[Suggestion] = Field(min_length=1, max_length=4)
    compatible: bool
    conflict: str | None
