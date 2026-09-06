from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish_style.py"
REVISION = "3a9c63baa03e6bbe2f28c89a2654cf9845466646"


def _load_publisher():
    spec = importlib.util.spec_from_file_location("publish_style", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load publisher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def publisher():
    return _load_publisher()


def _style(style_id: str = "drama_ext.celadon_shadow") -> dict[str, object]:
    return {
        "id": style_id,
        "name": "青瓷影",
        "category": "chinese",
        "summary": "青瓷釉色与克制暗影构成的东方视觉语言。",
        "prompt_fragment": {
            "medium": ["celadon glaze texture", "delicate mineral pigment"],
            "rendering": ["restrained contour rhythm", "subtle crackle detail"],
            "lighting": ["soft directional shadow", "quiet tonal separation"],
            "color": ["celadon green", "warm ivory", "charcoal accents"],
            "camera": ["balanced negative space", "layered planar depth"],
            "constraints": ["no readable text", "no logo", "story-neutral styling"],
        },
        "use_cases": ["东方悬疑", "含蓄叙事"],
        "preview_asset": "/images/extension-styles/celadon-shadow.webp",
        "source": {
            "repository": "freestylefly/awesome-gpt-image-2",
            "source_ids": ["illustration-art-style", "history-classical-themes"],
            "license_review": "approved",
            "imported_revision": REVISION,
        },
        "version": "1.0.0",
    }


def _base_style() -> dict[str, object]:
    style = _style("drama_ext.existing_style")
    style["name"] = "既有风格"
    style["preview_asset"] = "/images/extension-styles/existing-style.webp"
    return style


@pytest.fixture
def project(tmp_path: Path) -> Path:
    catalog = tmp_path / "src/novelvideo/extension_styles/catalog.json"
    audit = tmp_path / "src/novelvideo/extension_styles/source_audit.json"
    snapshot = (
        tmp_path
        / "frontend/src/features/canvas/extension-styles/catalog.generated.json"
    )
    preview_dir = tmp_path / "frontend/public/images/extension-styles"
    catalog.parent.mkdir(parents=True)
    snapshot.parent.mkdir(parents=True)
    preview_dir.mkdir(parents=True)
    entries = [_base_style()]
    rendered = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    catalog.write_text(rendered, encoding="utf-8")
    snapshot.write_text(rendered, encoding="utf-8")
    audit.write_text(
        json.dumps(
            {
                "repository": "freestylefly/awesome-gpt-image-2",
                "revision": REVISION,
                "license": {"review": "approved"},
                "templates": [
                    {"id": "illustration-art-style", "json_pointer": "/templates/15"},
                    {"id": "history-classical-themes", "json_pointer": "/templates/19"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _manifest(project: Path, style: dict[str, object] | None = None) -> Path:
    path = project / "release.json"
    path.write_text(
        json.dumps(
            {
                "style": style or _style(),
                "preview_prompt": "Original celadon-inspired abstract visual study.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _product_bytes(project: Path) -> dict[str, bytes | None]:
    paths = (
        "src/novelvideo/extension_styles/catalog.json",
        "src/novelvideo/extension_styles/source_audit.json",
        "frontend/src/features/canvas/extension-styles/catalog.generated.json",
        "frontend/public/images/extension-styles/celadon-shadow.webp",
    )
    return {
        relative: (project / relative).read_bytes()
        if (project / relative).exists()
        else None
        for relative in paths
    }


def _prepare(publisher, project: Path) -> tuple[dict[str, object], Path]:
    manifest = _manifest(project)
    plan = publisher.prepare_release(project, manifest)
    plan_path = project / "release-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan, plan_path


def test_prepare_writes_plan_without_mutating_project(publisher, project: Path):
    before = _product_bytes(project)
    manifest = _manifest(project)

    plan = publisher.prepare_release(project, manifest)

    assert _product_bytes(project) == before
    assert plan["schema_version"] == 1
    assert plan["catalog_sha256"] == hashlib.sha256(
        before["src/novelvideo/extension_styles/catalog.json"]
    ).hexdigest()
    assert plan["style"] == _style()
    assert plan["preview_prompt"] == (
        "Original celadon-inspired abstract visual study."
    )
    assert plan["targets"] == {
        "catalog": "src/novelvideo/extension_styles/catalog.json",
        "snapshot": (
            "frontend/src/features/canvas/extension-styles/catalog.generated.json"
        ),
        "preview": (
            "frontend/public/images/extension-styles/celadon-shadow.webp"
        ),
    }


def test_prepare_rejects_duplicate_id_without_mutation(publisher, project: Path):
    before = _product_bytes(project)
    manifest = _manifest(project, _base_style())

    with pytest.raises(publisher.PublishError, match="already exists") as error:
        publisher.prepare_release(project, manifest)

    assert error.value.exit_code == 2
    assert _product_bytes(project) == before


def test_prepare_rejects_source_not_in_audit(publisher, project: Path):
    style = _style()
    style["source"]["source_ids"] = ["unknown-template"]

    with pytest.raises(publisher.PublishError, match="source_ids"):
        publisher.prepare_release(project, _manifest(project, style))


def test_prepare_rejects_noncanonical_preview_path(publisher, project: Path):
    style = _style()
    style["preview_asset"] = "/images/extension-styles/wrong.webp"

    with pytest.raises(publisher.PublishError, match="canonical"):
        publisher.prepare_release(project, _manifest(project, style))


def test_apply_updates_three_products_and_normalizes_preview(
    publisher, project: Path
):
    plan, plan_path = _prepare(publisher, project)
    source = project / "preview.png"
    Image.new("RGBA", (800, 800), (90, 130, 115, 180)).save(source)
    audit_before = (
        project / "src/novelvideo/extension_styles/source_audit.json"
    ).read_bytes()

    summary = publisher.apply_release(project, plan_path, source)

    catalog = json.loads(
        (project / plan["targets"]["catalog"]).read_text(encoding="utf-8")
    )
    snapshot = json.loads(
        (project / plan["targets"]["snapshot"]).read_text(encoding="utf-8")
    )
    output = project / plan["targets"]["preview"]
    assert catalog == snapshot
    assert [entry["id"] for entry in catalog] == [
        "drama_ext.existing_style",
        "drama_ext.celadon_shadow",
    ]
    with Image.open(output) as image:
        assert image.format == "WEBP"
        assert image.size == (640, 360)
        assert image.mode == "RGB"
    assert (
        project / "src/novelvideo/extension_styles/source_audit.json"
    ).read_bytes() == audit_before
    assert summary == {"style_id": "drama_ext.celadon_shadow", "targets": plan["targets"]}


def test_apply_rejects_catalog_drift_without_writes(publisher, project: Path):
    _, plan_path = _prepare(publisher, project)
    catalog = project / "src/novelvideo/extension_styles/catalog.json"
    catalog.write_bytes(catalog.read_bytes() + b"\n")
    source = project / "preview.png"
    Image.new("RGB", (640, 360), "white").save(source)
    before = _product_bytes(project)

    with pytest.raises(publisher.PublishError, match="changed") as error:
        publisher.apply_release(project, plan_path, source)

    assert error.value.exit_code == 3
    assert _product_bytes(project) == before


def test_apply_rejects_invalid_image_without_writes(publisher, project: Path):
    _, plan_path = _prepare(publisher, project)
    source = project / "preview.png"
    source.write_text("not an image", encoding="utf-8")
    before = _product_bytes(project)

    with pytest.raises(publisher.PublishError, match="preview"):
        publisher.apply_release(project, plan_path, source)

    assert _product_bytes(project) == before


def test_apply_rolls_back_every_target_when_replace_fails(
    publisher, project: Path, monkeypatch: pytest.MonkeyPatch
):
    _, plan_path = _prepare(publisher, project)
    source = project / "preview.png"
    Image.new("RGB", (900, 500), "white").save(source)
    before = _product_bytes(project)
    real_replace = publisher._replace_bytes
    calls = 0

    def fail_second_replace(path: Path, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        real_replace(path, payload)

    monkeypatch.setattr(publisher, "_replace_bytes", fail_second_replace)

    with pytest.raises(publisher.PublishError, match="injected replacement failure"):
        publisher.apply_release(project, plan_path, source)

    assert _product_bytes(project) == before


def test_main_returns_stable_validation_exit_code(publisher, project: Path):
    style = _style()
    style["preview_asset"] = "/images/extension-styles/wrong.webp"
    plan_path = project / "plan.json"

    result = publisher.main(
        [
            "prepare",
            "--project-root",
            str(project),
            "--manifest",
            str(_manifest(project, style)),
            "--plan",
            str(plan_path),
        ]
    )

    assert result == 2
    assert not plan_path.exists()
