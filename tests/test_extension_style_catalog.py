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
        "ExtensionStyle",
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


CURATED_STYLE_NAMES = {
    "日系赛璐璐", "少女漫画", "热血少年漫", "韩式条漫",
    "欧美超级英雄漫画", "美式复古漫画", "法式绘本", "水彩故事书",
    "水墨国漫", "工笔重彩", "敦煌壁画国风", "2D 国漫厚涂",
    "3D 国漫动画", "黏土定格", "剪纸动画", "黑白悬疑漫画",
    "电影级写实", "复古胶片写实",
}
CURATED_REVISION = "3a9c63baa03e6bbe2f28c89a2654cf9845466646"


@pytest.fixture
def curated_catalog_path():
    return (
        Path(__file__).parents[1]
        / "src"
        / "novelvideo"
        / "extension_styles"
        / "catalog.json"
    )


def test_curated_catalog_has_exactly_the_required_styles(curated_catalog_path):
    catalog = load_catalog(curated_catalog_path)

    assert len(catalog) == 18
    assert {style.name for style in catalog} == CURATED_STYLE_NAMES
    assert len({style.id for style in catalog}) == 18
    assert all(style.id.startswith("drama_ext.") for style in catalog)


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


def test_existing_builtin_style_preset_json_set_is_unchanged():
    preset_dir = Path(__file__).parents[1] / "src" / "novelvideo" / "styles" / "presets"

    assert {path.stem for path in preset_dir.glob("*.json")} == {
        "anime",
        "chinese_period_drama",
        "guoman_fantasy",
        "post_apocalyptic",
        "realistic",
        "republican_era_drama",
    }
