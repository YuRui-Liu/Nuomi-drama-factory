import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DialogueCue,
    H3DirectorPlan,
    H3FrameDifference,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.models import H3Mode


def _camera() -> H3CameraPlan:
    return H3CameraPlan(
        type="push-in", direction="forward", amplitude="subtle", speed="slow"
    )


def _shot(
    *, shot_id: str = "1", start_frame: int = 0, end_frame: int = 101
) -> H3ShotPlan:
    return H3ShotPlan(
        shot_id=shot_id,
        start_frame=start_frame,
        end_frame=end_frame,
        framing="medium close-up",
        angle="eye level",
        focus="Lin Mo",
        composition="Lin Mo remains centered against the door",
        camera=_camera(),
        actions=(
            H3ActionPlan(
                phase="establish",
                start_frame=start_frame,
                end_frame=start_frame + 12,
                description="Lin Mo holds still against the door.",
            ),
            H3ActionPlan(
                phase="execute",
                start_frame=start_frame + 12,
                end_frame=end_frame,
                description="He turns toward the rattling handle.",
            ),
        ),
        dialogue=(),
    )


def test_models_are_frozen_and_forbid_extra_fields():
    camera = _camera()

    with pytest.raises(ValidationError, match="frozen"):
        camera.speed = "fast"
    with pytest.raises(ValidationError, match="extra"):
        H3CameraPlan(
            type="push-in",
            direction="forward",
            amplitude="subtle",
            speed="slow",
            easing="linear",
        )


def test_shots_must_contiguously_cover_total_frames_from_zero():
    with pytest.raises(ValidationError, match="contiguous.*frame 0"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(_shot(start_frame=1, end_frame=101),),
            soundscape="door rattle",
            music="low strings",
        )


def test_actions_and_dialogue_must_be_increasing_and_inside_their_shot():
    with pytest.raises(ValidationError, match="dialogue.*boundaries"):
        _shot().model_copy(
            update={
                "dialogue": (
                    H3DialogueCue(
                        start_frame=90,
                        end_frame=102,
                        speaker="Lin Mo",
                        speaker_id="S1",
                        text="Stay back.",
                        language="English",
                    ),
                )
            }
        ).__class__.model_validate(
            {
                **_shot().model_dump(),
                "dialogue": [
                    {
                        "start_frame": 90,
                        "end_frame": 102,
                        "speaker": "Lin Mo",
                        "speaker_id": "S1",
                        "text": "Stay back.",
                        "language": "English",
                    }
                ],
            }
        )


def test_dynamic_camera_requires_direction_amplitude_and_speed():
    with pytest.raises(ValidationError, match="dynamic camera"):
        H3CameraPlan(type="orbit", direction="clockwise", amplitude=None, speed="slow")


def test_i2va_requires_first_frame_establish_anchor_and_later_change():
    shot = _shot()
    only_anchor = shot.model_copy(update={"actions": (shot.actions[0],)})

    with pytest.raises(ValidationError, match="later visual change"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(only_anchor,),
            soundscape="door rattle",
            music="low strings",
        )


def test_fl2va_requires_one_shot_and_differences_converging_before_last_frame():
    base = dict(
        mode=H3Mode.FL2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        shots=(_shot(),),
        soundscape="door rattle",
        music="low strings",
    )

    with pytest.raises(ValidationError, match="frame differences"):
        H3DirectorPlan(**base)
    with pytest.raises(ValidationError, match="before the final frame"):
        H3DirectorPlan(
            **base,
            frame_differences=(
                H3FrameDifference(
                    description="The hand reaches the handle.",
                    convergence_frame=101,
                ),
            ),
        )


def test_speaker_identity_is_stable_across_shots():
    first = _shot(end_frame=50).model_copy(
        update={
            "dialogue": (
                H3DialogueCue(
                    start_frame=20,
                    end_frame=30,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="Stay back.",
                    language="English",
                ),
            )
        }
    )
    second = _shot(shot_id="2", start_frame=50, end_frame=101).model_copy(
        update={
            "dialogue": (
                H3DialogueCue(
                    start_frame=60,
                    end_frame=80,
                    speaker="Lin Mo",
                    speaker_id="S2",
                    text="I warned you.",
                    language="English",
                    continuation=True,
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="stable speaker_id"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(first, second),
            soundscape="door rattle",
            music="low strings",
        )
