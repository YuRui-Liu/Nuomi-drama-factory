from __future__ import annotations

import argparse
import shutil
from pathlib import Path


_DIRECTOR_DATA_NAMES = ("director_plans", ".narrative_groups")


def reset_test_director_data(
    project_dir: str | Path,
    *,
    confirmed_project_dir: str | Path,
) -> tuple[Path, ...]:
    """Delete only explicitly confirmed test DirectorPlan data for one project."""
    project = Path(project_dir).resolve()
    confirmed = Path(confirmed_project_dir).resolve()
    if project != confirmed:
        raise ValueError("confirmed project path must exactly match project path")
    if not project.is_dir():
        raise ValueError("project path must be an existing directory")
    if project.parent == project:
        raise ValueError("filesystem root cannot be reset")

    targets: list[Path] = []
    for name in _DIRECTOR_DATA_NAMES:
        unresolved = project / name
        if not unresolved.exists():
            continue
        target = unresolved.resolve()
        if target.parent != project:
            raise ValueError(f"refusing data path outside exact project: {target}")
        targets.append(target)

    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    return tuple(targets)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicitly remove test DirectorPlan and legacy sidecar data."
    )
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument(
        "--confirm-project-dir",
        required=True,
        type=Path,
        help="Must resolve to exactly the same project directory.",
    )
    args = parser.parse_args()
    removed = reset_test_director_data(
        args.project_dir, confirmed_project_dir=args.confirm_project_dir
    )
    for path in removed:
        print(f"removed {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
