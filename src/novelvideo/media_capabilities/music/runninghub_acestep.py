"""Compile the supplied ACE-Step 1.5 workflow without changing graph links."""
import secrets
from pydantic import Field, field_validator
from novelvideo.music.models import Model

DEFAULT_WORKFLOW_ID = '2059090557116440578'


class MusicRequest(Model):
    tags: str = Field(min_length=1, max_length=4000)
    bpm: int = Field(default=75, ge=30, le=300)
    durationSeconds: float = Field(default=30, gt=0, le=600)
    seed: str = Field(default_factory=lambda: str(secrets.randbits(63)))

    @field_validator('tags')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请输入音乐描述')
        return value.strip()

    @field_validator('seed')
    @classmethod
    def valid_seed(cls, value):
        if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 2**64-1:
            raise ValueError('种子必须为 uint64 十进制字符串')
        return value


def compile_request(request: MusicRequest) -> list[dict[str, str]]:
    fields = [('94', 'tags', f'Instrumental, no vocals. {request.tags}'), ('94', 'lyrics', '[Instrumental]'),
              ('203', 'value', str(request.bpm)), ('205', 'value', f'{request.durationSeconds:g}'), ('109', 'value', request.seed)]
    return [dict(nodeId=node, fieldName=field, fieldValue=value) for node, field, value in fields]


def validate_contract(graph: dict):
    types = {'94': 'TextEncodeAceStepAudio1.5', '98': 'EmptyAceStep1.5LatentAudio',
             '109': 'PrimitiveInt', '203': 'Int', '205': 'Float', '107': 'SaveAudioMP3'}
    for node, kind in types.items():
        if graph.get(node, {}).get('class_type') != kind:
            raise ValueError(f'配乐工作流节点 {node} 类型不匹配')
    for node, field, source in [('94', 'duration', '205'), ('98', 'seconds', '205'), ('94', 'seed', '109'), ('94', 'bpm', '203')]:
        if graph[node].get('inputs', {}).get(field) != [source, 0]:
            raise ValueError(f'配乐工作流连线 {node}.{field} 不匹配')
