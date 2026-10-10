"""Test-only complete QC evidence; never used to fabricate production reviews."""
from novelvideo.director_plan.creative_review import DIMENSIONS
from novelvideo.director_plan.revision_feedback import CreativeAssessment


def complete_assessments(candidate):
    shots = [(shot, f"groups.{gi}.shots.{si}") for gi, group in enumerate(candidate.groups)
             for si, shot in enumerate(group.shots)]
    result = []
    for index, (shot, location) in enumerate(shots):
        for dimension in DIMENSIONS:
            paths = [f"{location}.creative.{dimension}"]
            if dimension == "handoff" and index + 1 < len(shots):
                paths.append(f"{shots[index + 1][1]}.visible_start_state")
            result.append(CreativeAssessment(shot_id=shot.id, dimension=dimension,
                conclusion="passed", reason=f"Fixture verifies concrete {dimension} choices", evidence_paths=tuple(paths)))
    return tuple(result)
