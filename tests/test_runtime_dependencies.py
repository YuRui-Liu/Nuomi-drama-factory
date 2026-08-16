from pathlib import Path


def test_transformers_is_a_core_runtime_dependency() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    core, optional = pyproject.split("[project.optional-dependencies]", maxsplit=1)
    assert '"transformers>=4.57.0,<5.0.0"' in core
    assert '"transformers>=4.57.0,<5.0.0"' not in optional.split(
        "[dependency-groups]", maxsplit=1
    )[0]
