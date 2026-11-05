from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import technique_library as technique_library_routes
from novelvideo.freezone.video_director.techniques import (
    TechniqueApplicability,
    TechniqueCard,
    TechniqueSource,
)
from novelvideo.technique_library import catalog
from novelvideo.technique_library import experience


def _case(case_id: str, provenance: str = "author", mode: str | None = None) -> dict:
    return {
        "id": case_id,
        "title": f"Case {case_id}",
        "summary": f"Summary for {case_id}",
        "use_cases": ["performance"],
        "provenance": provenance,
        "sources": [{
            "repository": "example/cases",
            "revision": "a" * 40,
            "path": "cases.json",
            "upstream_id": f"source-{case_id}",
            "url": f"https://example.com/{case_id}",
            "provenance": provenance,
        }],
        "prompts": [{"text": "must not be returned", "provenance": provenance}],
        "mode": mode,
        "duration": "8s",
    }


def _technique(card_id: str, modes: tuple[str, ...], case_id: str) -> TechniqueCard:
    return TechniqueCard(
        id=card_id,
        version="1.0.0",
        status="active",
        title=card_id,
        summary=f"Summary for {card_id}",
        category="人物表演",
        intent="Keep the established story facts.",
        action_beats=("start", "finish"),
        performance="Use restrained movement.",
        camera="Keep the framing stable.",
        ending_composition="Preserve the final pose.",
        avoid=("inventing a new event",),
        applicability=TechniqueApplicability(
            modes=modes,
            min_duration_seconds=2,
            max_duration_seconds=12,
            last_frame_constraint="allowed",
        ),
        sources=(TechniqueSource(
            url="https://example.com/technique",
            credit="Example source",
            source_type="analysis_only",
            checked_at=date(2026, 10, 1),
            basis="Adapted as a reusable technique.",
        ),),
        case_ids=(case_id,),
    )


def test_search_returns_empty_results_without_marking_catalog_unavailable(monkeypatch):
    monkeypatch.setattr(catalog, "query_cases", lambda *args, **kwargs: {"items": []})

    result = experience.search_experience("missing", "i2v", 6)

    assert result == {"items": [], "unavailable": False}


def test_search_reports_unavailable_catalog_without_fabricating_cases(monkeypatch):
    def unavailable(*args, **kwargs):
        raise catalog.CatalogUnavailable("offline")

    monkeypatch.setattr(catalog, "query_cases", unavailable)

    result = experience.search_experience("reaction", "i2v", 6)

    assert result == {"items": [], "unavailable": True}


def test_search_preserves_source_ids_and_distinguishes_provenance(monkeypatch):
    cases = [_case("h3-author", "author"), _case("h3-rebuilt", "reconstructed")]
    monkeypatch.setattr(catalog, "query_cases", lambda *args, **kwargs: {"items": cases})

    result = experience.search_experience("performance", "i2v", 6)

    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["h3-author"]["sources"] == [{
        "id": "source-h3-author",
        "link": "https://example.com/h3-author",
        "provenance": "author",
    }]
    assert by_id["h3-rebuilt"]["sources"][0]["provenance"] == "reconstructed"
    assert by_id["h3-author"]["applicability"] == {
        "requested_mode": "i2v",
        "requested_duration_seconds": 6,
        "source_metadata": {"mode": None, "duration": "8s"},
        "use_cases": ["performance"],
    }
    assert "must not be returned" not in repr(result)


def test_search_excludes_techniques_incompatible_with_requested_mode(monkeypatch):
    case = _case("h3-case")
    monkeypatch.setattr(catalog, "query_cases", lambda *args, **kwargs: {"items": [case]})
    monkeypatch.setattr(experience.techniques, "list_techniques", lambda: (
        _technique("i2v-technique", ("i2v",), "h3-case"),
        _technique("fl2v-technique", ("fl2v",), "h3-case"),
    ))

    result = experience.search_experience("case", "i2v", 6)

    assert result["items"][0]["compatible_technique_ids"] == ["i2v-technique"]
    assert result["items"][0]["applicability"]["requested_mode"] == "i2v"


def test_search_accepts_supported_reference_frame_modes_as_ref_techniques(monkeypatch):
    case = _case("h3-case", mode="ref2va")
    monkeypatch.setattr(catalog, "query_cases", lambda *args, **kwargs: {"items": [case]})
    monkeypatch.setattr(experience.techniques, "list_techniques", lambda: (
        _technique("reference-technique", ("ref_only",), "h3-case"),
        _technique("image-technique", ("i2v",), "h3-case"),
    ))

    result = experience.search_experience("case", "ref_plus_first_last", 6)

    assert result["items"][0]["compatible_technique_ids"] == ["reference-technique"]
    assert result["items"][0]["applicability"]["requested_mode"] == "ref_plus_first_last"


def test_search_respects_technique_last_frame_constraint_for_reference_modes(monkeypatch):
    case = _case("h3-case", mode="ref2va")
    forbidden = _technique("forbidden-last", ("ref_only",), "h3-case").model_copy(update={
        "applicability": TechniqueApplicability(
            modes=("ref_only",), min_duration_seconds=2, max_duration_seconds=12,
            last_frame_constraint="forbidden",
        ),
    })
    monkeypatch.setattr(catalog, "query_cases", lambda *args, **kwargs: {"items": [case]})
    monkeypatch.setattr(experience.techniques, "list_techniques", lambda: (forbidden,))

    with_last_frame = experience.search_experience("case", "ref_plus_first_last", 6)
    without_last_frame = experience.search_experience("case", "ref_only", 6)

    assert with_last_frame["items"][0]["compatible_technique_ids"] == []
    assert without_last_frame["items"][0]["compatible_technique_ids"] == ["forbidden-last"]


def test_search_excludes_catalog_cases_with_a_known_incompatible_mode(monkeypatch):
    monkeypatch.setattr(catalog, "query_cases", lambda *args, **kwargs: {
        "items": [_case("h3-case", mode="i2va")],
    })

    result = experience.search_experience("case", "ref_only", 6)

    assert result["items"] == []


def test_experience_route_is_authenticated_and_preserves_unavailable_state(monkeypatch):
    app = FastAPI()
    app.include_router(technique_library_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"user_id": "alice"}
    monkeypatch.setattr(
        technique_library_routes.experience_service,
        "search_experience",
        lambda query, mode, duration_seconds, limit: {
            "items": [],
            "unavailable": True,
        },
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/techniques/experience?query=反应&mode=ref_plus_first&duration_seconds=6&limit=4"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"items": [], "unavailable": True}}


def test_project_scoped_experience_and_case_routes_require_project_view_access(monkeypatch):
    from novelvideo.api import api_router

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_api_user] = lambda: {"user_id": "alice"}
    resolved = []

    async def resolve_project(project, user, *, required_role):
        resolved.append((project, required_role))
        return None

    monkeypatch.setattr(technique_library_routes, "_resolve_freezone_project", resolve_project)
    monkeypatch.setattr(
        technique_library_routes.experience_service,
        "search_experience",
        lambda query, mode, duration_seconds, limit: {"items": [], "unavailable": False},
    )
    monkeypatch.setattr(technique_library_routes.case_catalog, "get_case", lambda case_id: _case(case_id))
    client = TestClient(app)

    experience_response = client.get(
        "/api/v1/projects/project-1/techniques/experience?query=反应&mode=i2v&duration_seconds=6"
    )
    case_response = client.get("/api/v1/projects/project-1/techniques/cases/case-1")

    assert experience_response.status_code == 200
    assert case_response.status_code == 200
    assert resolved == [("project-1", "viewer"), ("project-1", "viewer")]

