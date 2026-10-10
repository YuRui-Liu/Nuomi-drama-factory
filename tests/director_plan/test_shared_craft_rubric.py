from novelvideo.director_plan.prompts import build_episode_prompt
from novelvideo.director_plan.creative_review import repair_prompt, review_prompt
from tests.director_plan.test_repair_convergence import history_for
from tests.director_plan.test_service import episode, group
from novelvideo.director_plan.service import DirectorPlanService


def test_first_draft_repair_and_qc_share_identical_acceptance_rules():
    from novelvideo.director_plan.prompts import CRAFT_ACCEPTANCE_RUBRIC
    value = episode().model_copy(update={"creative_contract_version": 1})
    candidate = DirectorPlanService._make_revision(value, (group("g1", 1, "s1"),))
    history = history_for(candidate)
    for prompt in (build_episode_prompt(value), repair_prompt(value, candidate, history),
                   review_prompt(value, candidate, history)):
        assert prompt.count(CRAFT_ACCEPTANCE_RUBRIC) == 1
        for dimension in ("optics", "framing", "camera_position", "composition", "motion",
                          "performance", "lighting", "handoff"):
            assert f"[{dimension}]" in prompt
        assert "Do not introduce new acceptance requirements" in prompt
        assert "SELF-CHECK BEFORE SUBMISSION" in prompt
