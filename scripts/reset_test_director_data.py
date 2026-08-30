from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


_DIRECTOR_DATA_NAMES = ("director_plans", ".narrative_groups")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _reject_existing_reparse_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if os.path.lexists(current) and _is_reparse_point(current):
            raise ValueError(f"symlink or reparse point is not allowed: {current}")


def _reject_reparse_tree(root: Path) -> None:
    if _is_reparse_point(root):
        raise ValueError(f"symlink or reparse point is not allowed: {root}")
    if not root.is_dir():
        return
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in (*dirnames, *filenames):
            candidate = base / name
            if _is_reparse_point(candidate):
                raise ValueError(
                    f"symlink or reparse point is not allowed: {candidate}"
                )


def reset_test_director_data(
    project_dir: str | Path,
    *,
    confirmed_project_dir: str | Path,
) -> tuple[Path, ...]:
    """Delete only explicitly confirmed test DirectorPlan data for one project."""
    raw_project = Path(project_dir).absolute()
    raw_confirmed = Path(confirmed_project_dir).absolute()
    _reject_existing_reparse_components(raw_project)
    _reject_existing_reparse_components(raw_confirmed)
    project = raw_project.resolve()
    confirmed = raw_confirmed.resolve()
    if project != confirmed:
        raise ValueError("confirmed project path must exactly match project path")
    if not project.is_dir():
        raise ValueError("project path must be an existing directory")
    if project.parent == project:
        raise ValueError("filesystem root cannot be reset")

    targets: list[Path] = []
    for name in _DIRECTOR_DATA_NAMES:
        unresolved = project / name
        if not os.path.lexists(unresolved):
            continue
        _reject_reparse_tree(unresolved)
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
