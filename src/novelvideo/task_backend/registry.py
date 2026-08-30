"""Task runner registry shared by backend adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ProjectTaskRunner = Callable[[dict[str, Any], Any], dict[str, Any] | None]


@dataclass(frozen=True)
class ProjectTaskRunnerRegistration:
    runner: ProjectTaskRunner
    text_task_role: str | None = None


_PROJECT_TASK_RUNNERS: dict[str, ProjectTaskRunnerRegistration] = {}


def register_project_task_runner(
    task_type: str,
    runner: ProjectTaskRunner,
    *,
    text_task_role: str | None = None,
) -> None:
    _PROJECT_TASK_RUNNERS[task_type] = ProjectTaskRunnerRegistration(
        runner=runner,
        text_task_role=str(text_task_role or "").strip() or None,
    )


def get_project_task_runner(task_type: str) -> ProjectTaskRunner | None:
    registration = get_project_task_runner_registration(task_type)
    return registration.runner if registration is not None else None


def get_project_task_runner_registration(
    task_type: str,
) -> ProjectTaskRunnerRegistration | None:
    return _PROJECT_TASK_RUNNERS.get(task_type)


def registered_project_task_types() -> tuple[str, ...]:
    return tuple(sorted(_PROJECT_TASK_RUNNERS))
