import hashlib
import json
import math
from pathlib import Path

import pytest

import novelvideo.extension_styles as extension_styles
from novelvideo.extension_styles import (
    FRAGMENT_KEYS,
    ExtensionStyle,
    compile_prompt_fragment,
    load_catalog,
)


def valid_style(**overrides):
    data = {
        "id": "drama_ext.ink_wash",
        "name": "Ink Wash",
        "category": "chinese",
        "summary": "Expressive ink texture with restrained tonal transitions.",
        "prompt_fragment": {
            "medium": ["ink wash painting", "rice-paper texture"],
            "rendering": ["expressive brushwork"],
            "lighting": [],
            "color": ["restrained ink tones"],
            "camera": ["layered depth"],
            "constraints": ["clean silhouettes"],
        },
        "use_cases": ["dramatic atmosphere", "poetic imagery"],
        "preview_asset": "/images/extension-styles/ink-wash.webp",
        "source": {
            "license_review": "approved",
            "imported_revision": "a" * 40,
        },
        "version": "1.0.0",
    }
    data.update(overrides)
    return data


class MutableMetadata:
    def __init__(self):
        self.value = "mutable"


def test_parses_valid_style_and_freezes_sequences():
    style = ExtensionStyle.from_dict(valid_style())

    assert style.id == "drama_ext.ink_wash"
    assert style.prompt_fragment["medium"] == (
        "ink wash painting",
        "rice-paper texture",
    )
    assert style.use_cases == ("dramatic atmosphere", "poetic imagery")
    assert FRAGMENT_KEYS == (
        "medium",
        "rendering",
        "lighting",
        "color",
        "camera",
        "constraints",
    )
    with pytest.raises(AttributeError):
        style.name = "Changed"


def test_public_api_is_explicitly_exported():
    assert set(extension_styles.__all__) == {
        "CatalogDiagnostics",
        "CatalogError",
        "CatalogFingerprint",
        "CatalogSnapshot",
        "ExtensionStyle",
        "ExtensionStyleRegistry",
        "compile_prompt_fragment",
        "load_catalog",
        "FRAGMENT_KEYS",
    }


def test_source_is_recursively_immutable():
    data = valid_style()
    data["source"]["provenance"] = {
        "authors": ["Example Author"],
    }
    style = ExtensionStyle.from_dict(data)

    with pytest.raises(TypeError):
        style.source["license_review"] = "pending"
    with pytest.raises(TypeError):
        style.source["provenance"]["authors"] = ()
    with pytest.raises(TypeError):
        style.source["provenance"]["authors"][0] = "Changed"


@pytest.mark.parametrize(
    "unsupported",
    [
        {"unapproved"},
        bytearray(b"mutable"),
        MutableMetadata(),
    ],
    ids=["set", "bytearray", "custom-mutable-object"],
)
def test_source_rejects_non_json_compatible_values(unsupported):
    data = valid_style()
    data["source"]["metadata"] = unsupported

    with pytest.raises(ValueError, match="source.*JSON-compatible values"):
        ExtensionStyle.from_dict(data)


@pytest.mark.parametrize("unsupported", [math.nan, math.inf, -math.inf])
def test_source_rejects_non_finite_floats(unsupported):
    data = valid_style()
    data["source"]["confidence"] = unsupported

    with pytest.raises(ValueError, match="source.*finite"):
        ExtensionStyle.from_dict(data)


def test_compiles_fragments_in_fixed_order_and_omits_empty_phrases():
    style = ExtensionStyle.from_dict(valid_style())

    assert compile_prompt_fragment(style) == (
        "ink wash painting, rice-paper texture, expressive brushwork, "
        "restrained ink tones, layered depth, clean silhouettes"
    )


@pytest.mark.parametrize(
    "fragments",
    [
        {**valid_style()["prompt_fragment"], "medium": "ink wash"},
        {
            key: value
            for key, value in valid_style()["prompt_fragment"].items()
            if key != "camera"
        },
    ],
    ids=["bare-string-value", "missing-key"],
)
def test_compile_rejects_invalid_fragment_mappings_with_value_error(fragments):
    with pytest.raises(ValueError, match="prompt_fragment"):
        compile_prompt_fragment(fragments)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": "ink_wash"}, "drama_ext"),
        (
            {
                "source": {
                    "license_review": "pending",
                    "imported_revision": "a" * 40,
                }
            },
            "license_review",
        ),
        (
            {
                "source": {
                    "license_review": "approved",
                    "imported_revision": "ABC123",
                }
            },
            "imported_revision",
        ),
        ({"preview_asset": "/images/ink-wash.png"}, "preview_asset"),
        ({"category": "anime"}, "category"),
    ],
)
def test_rejects_invalid_catalog_metadata(overrides, message):
    with pytest.raises(ValueError, match=message):
        ExtensionStyle.from_dict(valid_style(**overrides))


def test_rejects_story_content_bias_in_prompt_fragments():
    data = valid_style()
    data["prompt_fragment"]["rendering"] = [
        "a princess wearing hanfu in a Tang dynasty palace"
    ]

    with pytest.raises(ValueError, match="story content"):
        ExtensionStyle.from_dict(data)


@pytest.mark.parametrize(
    ("dimension", "phrase"),
    [
        ("character", "a cowboy"),
        ("era", "1920s aesthetic"),
        ("location", "New York street"),
        ("costume", "tuxedo"),
        ("prop", "dagger"),
        ("action", "dancing"),
    ],
)
def test_rejects_each_story_content_bias_dimension(dimension, phrase):
    data = valid_style()
    data["prompt_fragment"]["rendering"] = [phrase]

    with pytest.raises(ValueError, match=rf"story content bias.*{dimension}"):
        ExtensionStyle.from_dict(data)


@pytest.mark.parametrize(
    "phrases",
    [
        ["holding", "a soft glow"],
        ["tang", "dynasty texture"],
    ],
    ids=["holding-split", "dynasty-split"],
)
def test_story_bias_terms_do_not_match_across_phrases(phrases):
    data = valid_style()
    data["prompt_fragment"]["rendering"] = phrases

    ExtensionStyle.from_dict(data)


@pytest.mark.parametrize("phrase", ["princess2", "princess_name"])
def test_english_story_terms_require_token_boundaries(phrase):
    data = valid_style()
    data["prompt_fragment"]["rendering"] = [phrase]

    ExtensionStyle.from_dict(data)


@pytest.mark.parametrize("phrase", ["princesses", "soldiers", "fights"])
def test_rejects_common_story_term_inflections(phrase):
    data = valid_style()
    data["prompt_fragment"]["rendering"] = [phrase]

    with pytest.raises(ValueError, match="story content bias"):
        ExtensionStyle.from_dict(data)


def test_chinese_story_term_does_not_match_inside_larger_word():
    data = valid_style()
    data["prompt_fragment"]["rendering"] = ["公主岭般的层叠山势"]

    ExtensionStyle.from_dict(data)


@pytest.mark.parametrize(
    "phrase",
    ["手持宝剑", "唐朝皇宫", "公主穿汉服"],
)
def test_rejects_story_terms_embedded_in_continuous_chinese(phrase):
    data = valid_style()
    data["prompt_fragment"]["rendering"] = [phrase]

    with pytest.raises(ValueError, match="story content bias"):
        ExtensionStyle.from_dict(data)


@pytest.mark.parametrize(
    "category",
    ["2d", "3d", "realistic", "chinese", "experimental"],
)
def test_accepts_every_supported_category(category):
    style = ExtensionStyle.from_dict(valid_style(category=category))

    assert style.category == category


@pytest.mark.parametrize("revision", ["a" * 39, "A" * 40])
def test_rejects_short_or_uppercase_revision(revision):
    data = valid_style()
    data["source"]["imported_revision"] = revision

    with pytest.raises(ValueError, match="imported_revision"):
        ExtensionStyle.from_dict(data)


def test_requires_exact_fragment_keys():
    missing = valid_style()
    del missing["prompt_fragment"]["camera"]
    with pytest.raises(ValueError, match="exactly"):
        ExtensionStyle.from_dict(missing)

    extra = valid_style()
    extra["prompt_fragment"]["story"] = []
    with pytest.raises(ValueError, match="exactly"):
        ExtensionStyle.from_dict(extra)


def test_requires_fragment_values_to_be_string_arrays():
    not_array = valid_style()
    not_array["prompt_fragment"]["medium"] = "ink wash"
    with pytest.raises(ValueError, match="array of strings"):
        ExtensionStyle.from_dict(not_array)

    non_string = valid_style()
    non_string["prompt_fragment"]["medium"] = [42]
    with pytest.raises(ValueError, match="only strings"):
        ExtensionStyle.from_dict(non_string)


def test_converts_all_fragment_values_to_tuples():
    style = ExtensionStyle.from_dict(valid_style())

    assert tuple(style.prompt_fragment) == FRAGMENT_KEYS
    assert all(isinstance(style.prompt_fragment[key], tuple) for key in FRAGMENT_KEYS)


def test_load_catalog_reads_utf8_array_as_tuple(tmp_path):
    data = valid_style(name="水墨风格", summary="克制的水墨视觉处理。")
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps([data], ensure_ascii=False), encoding="utf-8")

    catalog = load_catalog(path)

    assert isinstance(catalog, tuple)
    assert len(catalog) == 1
    assert catalog[0].name == "水墨风格"


def test_load_catalog_rejects_non_array(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(valid_style()), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON array"):
        load_catalog(path)


def test_load_catalog_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps([valid_style(), valid_style()], ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate.*drama_ext.ink_wash"):
        load_catalog(path)


REQUIRED_BASELINE_CATALOG = {
    "drama_ext.japanese_cel_animation": (
        "日系赛璐璐",
        "2d",
        ("illustration-art-style", "character-design-sheet"),
        "/images/extension-styles/japanese-cel-animation.webp",
    ),
    "drama_ext.shojo_manga": (
        "少女漫画",
        "2d",
        (
            "illustration-art-style",
            "character-design-sheet",
            "scene-storytelling",
        ),
        "/images/extension-styles/shojo-manga.webp",
    ),
    "drama_ext.shonen_manga": (
        "热血少年漫",
        "2d",
        (
            "illustration-art-style",
            "character-design-sheet",
            "scene-storytelling",
        ),
        "/images/extension-styles/shonen-manga.webp",
    ),
    "drama_ext.korean_webtoon": (
        "韩式条漫",
        "2d",
        (
            "illustration-art-style",
            "character-design-sheet",
            "scene-storytelling",
        ),
        "/images/extension-styles/korean-webtoon.webp",
    ),
    "drama_ext.western_superhero_comic": (
        "欧美超级英雄漫画",
        "2d",
        (
            "illustration-art-style",
            "character-design-sheet",
            "scene-storytelling",
        ),
        "/images/extension-styles/western-superhero-comic.webp",
    ),
    "drama_ext.american_retro_comic": (
        "美式复古漫画",
        "2d",
        ("illustration-art-style",),
        "/images/extension-styles/american-retro-comic.webp",
    ),
    "drama_ext.french_picture_book": (
        "法式绘本",
        "experimental",
        ("illustration-art-style", "scene-storytelling"),
        "/images/extension-styles/french-picture-book.webp",
    ),
    "drama_ext.watercolor_storybook": (
        "水彩故事书",
        "experimental",
        ("illustration-art-style", "scene-storytelling"),
        "/images/extension-styles/watercolor-storybook.webp",
    ),
    "drama_ext.ink_wash_guoman": (
        "水墨国漫",
        "chinese",
        (
            "illustration-art-style",
            "ink-double-exposure-poster",
            "history-classical-themes",
        ),
        "/images/extension-styles/ink-wash-guoman.webp",
    ),
    "drama_ext.gongbi_heavy_color": (
        "工笔重彩",
        "chinese",
        ("illustration-art-style", "history-classical-themes"),
        "/images/extension-styles/gongbi-heavy-color.webp",
    ),
    "drama_ext.dunhuang_mural": (
        "敦煌壁画国风",
        "chinese",
        ("illustration-art-style", "history-classical-themes"),
        "/images/extension-styles/dunhuang-mural.webp",
    ),
    "drama_ext.guoman_2d_painterly": (
        "2D 国漫厚涂",
        "2d",
        (
            "illustration-art-style",
            "character-design-sheet",
            "scene-storytelling",
        ),
        "/images/extension-styles/guoman-2d-painterly.webp",
    ),
    "drama_ext.guoman_3d_animation": (
        "3D 国漫动画",
        "3d",
        (
            "3d-collectible-toy",
            "character-design-sheet",
            "scene-storytelling",
        ),
        "/images/extension-styles/guoman-3d-animation.webp",
    ),
    "drama_ext.clay_stop_motion": (
        "黏土定格",
        "3d",
        ("3d-collectible-toy", "scene-storytelling"),
        "/images/extension-styles/clay-stop-motion.webp",
    ),
    "drama_ext.paper_cut_animation": (
        "剪纸动画",
        "chinese",
        (
            "illustration-art-style",
            "scene-storytelling",
            "history-classical-themes",
        ),
        "/images/extension-styles/paper-cut-animation.webp",
    ),
    "drama_ext.noir_suspense_manga": (
        "黑白悬疑漫画",
        "2d",
        ("illustration-art-style", "scene-storytelling"),
        "/images/extension-styles/noir-suspense-manga.webp",
    ),
    "drama_ext.cinematic_photorealism": (
        "电影级写实",
        "realistic",
        ("realistic-photography", "scene-storytelling"),
        "/images/extension-styles/cinematic-photorealism.webp",
    ),
    "drama_ext.vintage_film_realism": (
        "复古胶片写实",
        "realistic",
        ("realistic-photography",),
        "/images/extension-styles/vintage-film-realism.webp",
    ),
    "drama_ext.jinshi_ink_suspense": (
        "金石证痕",
        "chinese",
        (
            "illustration-art-style",
            "ink-double-exposure-poster",
            "character-design-sheet",
            "scene-storytelling",
            "history-classical-themes",
        ),
        "/images/extension-styles/jinshi-ink-suspense.webp",
    ),
}


def _assert_required_catalog_baseline(actual):
    assert set(REQUIRED_BASELINE_CATALOG) <= set(actual)
    assert {
        style_id: actual[style_id]
        for style_id in REQUIRED_BASELINE_CATALOG
    } == REQUIRED_BASELINE_CATALOG


def test_required_catalog_baseline_allows_contract_safe_additions():
    actual = dict(REQUIRED_BASELINE_CATALOG)
    actual["drama_ext.future_style"] = (
        "未来风格",
        "experimental",
        ("illustration-art-style",),
        "/images/extension-styles/future-style.webp",
    )

    _assert_required_catalog_baseline(actual)


def test_real_catalog_accepts_a_contract_safe_twentieth_style(
    curated_catalog_path, tmp_path
):
    raw = json.loads(curated_catalog_path.read_text(encoding="utf-8"))
    additional = json.loads(json.dumps(raw[-1], ensure_ascii=False))
    additional.update(
        {
            "id": "drama_ext.future_style",
            "name": "未来风格",
            "preview_asset": "/images/extension-styles/future-style.webp",
        }
    )
    raw.append(additional)
    expanded_catalog = tmp_path / "catalog.json"
    expanded_catalog.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    catalog = load_catalog(expanded_catalog)
    actual = {
        style.id: (
            style.name,
            style.category,
            tuple(style.source["source_ids"]),
            style.preview_asset,
        )
        for style in catalog
    }

    _assert_required_catalog_baseline(actual)
    assert actual["drama_ext.future_style"][0] == "未来风格"


CURATED_REVISION = "3a9c63baa03e6bbe2f28c89a2654cf9845466646"
BUILTIN_PRESET_CANONICAL_SHA256 = {
    # Baseline captured from DramaClaw commit 652dd48 before this feature.
    "anime.json": "41dcf96df13b4413f731893d61c6953c6d727da1ed0d5f96fab68723c2abbe88",
    "chinese_period_drama.json": (
        "8a2affc2380c1a091c8d53b7f9fc5a10cd8f79cfe09d7e805a6069fe5e00341e"
    ),
    "guoman_fantasy.json": (
        "8c1ec6b64b2fdf2354a74303f2c9e1b497e07cc70289920467f4527cc57d935b"
    ),
    "post_apocalyptic.json": (
        "637bef3e541ae296a9d45cd97ddb2e92593c030564b74948a1f1f7c01e6f362e"
    ),
    "realistic.json": (
        "d793012686f8b7130c67dfc7f2153d055e991976d7ed92455b645740665d0e06"
    ),
    "republican_era_drama.json": (
        "6493b5359ec9b58db569b2e119124a2adfce4234cab9addfa1fde4c83b0dd7f5"
    ),
}
EXPECTED_SOURCE_TEMPLATES = {
    "ink-double-exposure-poster": "/templates/6",
    "realistic-photography": "/templates/13",
    "illustration-art-style": "/templates/15",
    "character-design-sheet": "/templates/16",
    "3d-collectible-toy": "/templates/17",
    "scene-storytelling": "/templates/18",
    "history-classical-themes": "/templates/19",
}


@pytest.fixture
def curated_catalog_path():
    return (
        Path(__file__).parents[1]
        / "src"
        / "novelvideo"
        / "extension_styles"
        / "catalog.json"
    )


@pytest.fixture
def source_audit_path(curated_catalog_path):
    return curated_catalog_path.with_name("source_audit.json")


def test_curated_catalog_has_exactly_the_required_styles(curated_catalog_path):
    catalog = load_catalog(curated_catalog_path)

    actual = {
        style.id: (
            style.name,
            style.category,
            tuple(style.source["source_ids"]),
            style.preview_asset,
        )
        for style in catalog
    }
    _assert_required_catalog_baseline(actual)
    assert len({style.preview_asset for style in catalog}) == len(catalog)


def test_curated_source_ids_resolve_to_locked_upstream_manifest(
    curated_catalog_path,
    source_audit_path,
):
    catalog = load_catalog(curated_catalog_path)
    audit = json.loads(source_audit_path.read_text(encoding="utf-8"))

    assert audit["repository"] == "freestylefly/awesome-gpt-image-2"
    assert audit["revision"] == CURATED_REVISION
    assert audit["source_file"] == "data/style-library.json"
    assert audit["immutable_url"] == (
        "https://raw.githubusercontent.com/freestylefly/awesome-gpt-image-2/"
        f"{CURATED_REVISION}/data/style-library.json"
    )
    assert audit["license"]["spdx"] == "MIT"
    assert audit["license"]["review"] == "approved"
    assert audit["license"]["immutable_url"] == (
        "https://github.com/freestylefly/awesome-gpt-image-2/blob/"
        f"{CURATED_REVISION}/LICENSE"
    )
    assert audit["derivation"]
    audited_templates = {
        template["id"]: template["json_pointer"] for template in audit["templates"]
    }
    assert audited_templates == EXPECTED_SOURCE_TEMPLATES
    audited_ids = set(audited_templates)
    for style in catalog:
        assert style.source["repository"] == audit["repository"]
        assert style.source["imported_revision"] == audit["revision"]
        assert set(style.source["source_ids"]) <= audited_ids


def test_curated_catalog_entries_have_complete_provenance_and_fragments(
    curated_catalog_path,
):
    catalog = load_catalog(curated_catalog_path)

    for style in catalog:
        assert style.category in {"2d", "3d", "realistic", "chinese", "experimental"}
        assert tuple(style.prompt_fragment) == FRAGMENT_KEYS
        assert all(style.prompt_fragment[key] for key in FRAGMENT_KEYS)
        assert style.use_cases
        assert style.preview_asset.startswith("/images/extension-styles/")
        assert style.preview_asset.endswith(".webp")
        assert style.source["repository"] == "freestylefly/awesome-gpt-image-2"
        assert style.source["source_ids"]
        assert style.source["license_review"] == "approved"
        assert style.source["imported_revision"] == CURATED_REVISION
        assert style.version == "1"
        assert compile_prompt_fragment(style)
        assert "preserve identity" in " ".join(style.prompt_fragment["constraints"])
        assert "wardrobe/props/action/setting from base prompt" in " ".join(
            style.prompt_fragment["constraints"]
        )


def test_existing_builtin_style_preset_semantics_are_unchanged():
    preset_dir = Path(__file__).parents[1] / "src" / "novelvideo" / "styles" / "presets"

    actual = {
        path.name: hashlib.sha256(
            json.dumps(
                json.loads(path.read_text(encoding="utf-8")),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        for path in preset_dir.glob("*.json")
    }

    assert actual == BUILTIN_PRESET_CANONICAL_SHA256
