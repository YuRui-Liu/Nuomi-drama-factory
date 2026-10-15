import pytest

from novelvideo.freezone.video_director.reference_plan import compile_reference_plan, ReferencePlan


def test_offscreen_identity_and_frozen_dialogue_compile_without_model_tags():
    plan = ReferencePlan.model_validate(dict(
        summary='雪地画马车', action='蓝袖的手完成马车图案。',
        subjects=[dict(subject_id='lia', presence='offscreen', action='画外提问'),
                  dict(subject_id='noel', presence='visible', action='只有手入镜，画马车')],
        soundscape='炭条摩擦声', music='N/A'))
    facts = [dict(subject_tag='<Subject 1>', picture_tag='<Picture 1>', image_id='lia', variant_label=None),
             dict(subject_tag='<Subject 2>', picture_tag='<Picture 2>', image_id='noel', variant_label=None)]
    wire = compile_reference_plan(plan, facts, {'lia':'莉娅', 'noel':'诺尔'},
                                  [{'speaker':'莉娅','quote':'‘停车的地方？’'}], 3)
    assert '<Subject 1>' in wire.detailed_description
    assert '画外，不入镜' in wire.detailed_description
    assert '莉娅：‘停车的地方？’' in wire.overall_soundscape
    assert wire.subject_definitions.splitlines() == ['<Subject 1>: 莉娅 <Picture 1>', '<Subject 2>: 诺尔 <Picture 2>']


def test_unknown_subject_fails_closed():
    plan = ReferencePlan.model_validate(dict(summary='场景', action='保持',
        subjects=[dict(subject_id='invented', presence='visible', action='站立')], soundscape='风声', music='N/A'))
    with pytest.raises(ValueError, match='subject IDs'):
        compile_reference_plan(plan, [], {}, [], 4)


@pytest.mark.asyncio
async def test_optimizer_compiles_named_refs_and_preserves_four_segments():
    from novelvideo.freezone.video_director.optimizer import optimize
    from novelvideo.freezone.video_director.models import DirectorDraft, DirectorImage, DirectorSegment
    from novelvideo.knowledge_runtime.codex import StructuredImage
    refs = tuple(DirectorImage(image_id=name, url='/'+name) for name in ('lia','noel'))
    class Runtime:
        async def run_structured(self, *, output_type, **kwargs):
            return output_type.model_validate(dict(summary='画马车', action='蓝袖的手移动。',
                subjects=[dict(subject_id='lia', presence='offscreen', action='画外提问'),
                          dict(subject_id='noel', presence='visible', action='手入镜')],
                soundscape='风声', music='N/A'))
    draft = DirectorDraft(revision=7, aspect_ratio='16:9', resolution='720p', references=refs,
        segments=tuple(DirectorSegment(id=f's{i}', duration_seconds=3,
            prompt='<Picture 1>是莉娅：红斗篷；<Picture 2>是诺尔：蓝衣。莉娅画外问：‘停车的地方？’') for i in range(4)))
    result = await optimize(Runtime(), draft, frozen_images={name:StructuredImage(b'x','image/png') for name in ('lia','noel')})
    assert [s.segment_id for s in result.segments] == ['s0','s1','s2','s3']
    assert all('莉娅：‘停车的地方？’' in s.wire.overall_soundscape for s in result.segments)
