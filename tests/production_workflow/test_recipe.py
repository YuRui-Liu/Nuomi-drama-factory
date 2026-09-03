from __future__ import annotations

import importlib
import json

import pytest


def _recipe_module():
    return importlib.import_module("novelvideo.production_workflow.recipe")


def test_builtin_recipes_are_complete_and_balanced_is_default():
    recipe_module = _recipe_module()

    assert set(recipe_module.BUILTIN_RECIPES) == {
        "consistency_first",
        "balanced",
        "motion_enhanced",
        "cost_first",
    }
    assert recipe_module.DEFAULT_RECIPE_ID == "balanced"
    assert recipe_module.resolve_recipe() == recipe_module.BUILTIN_RECIPES["balanced"]

    required_fields = {
        "character",
        "scene",
        "props",
        "shot_grammar",
        "narrative_groups",
        "video_strategy",
        "reference_priority",
        "qc",
        "cost_weights",
    }
    assert required_fields <= set(recipe_module.ProductionRecipe.model_fields)

    for recipe in recipe_module.BUILTIN_RECIPES.values():
        assert recipe.revision
        assert all(getattr(recipe, field) for field in required_fields)
        assert sum(recipe.cost_weights.model_dump().values()) == pytest.approx(1.0)


def test_builtin_recipes_are_provider_agnostic():
    recipe_module = _recipe_module()
    payload = json.dumps(
        [recipe.model_dump(mode="json") for recipe in recipe_module.BUILTIN_RECIPES.values()],
        ensure_ascii=False,
    ).lower()

    for forbidden in (
        "api_key",
        "secret",
        "runninghub",
        "node_id",
        "provider",
        "model_call",
    ):
        assert forbidden not in payload


def test_registry_resolves_exact_recipe_revisions_without_overwriting_old_projects():
    recipe_module = _recipe_module()
    revision_1 = recipe_module.BUILTIN_RECIPES["balanced"]
    revision_2 = revision_1.model_copy(update={"revision": "2"})
    registry = recipe_module.ProductionRecipeRegistry([revision_1, revision_2])

    assert registry.resolve("balanced", "1") == revision_1
    assert registry.resolve("balanced", "2") == revision_2

    with pytest.raises(ValueError, match="already registered"):
        registry.register(revision_1)


def test_resolve_recipe_rejects_unknown_id_or_revision():
    recipe_module = _recipe_module()

    with pytest.raises(KeyError, match="unknown production recipe"):
        recipe_module.resolve_recipe("missing", "1")
    with pytest.raises(KeyError, match="unknown production recipe"):
        recipe_module.resolve_recipe("balanced", "missing")
