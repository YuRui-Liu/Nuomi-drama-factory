import json

import pytest

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


def test_compiles_fragments_in_fixed_order_and_omits_empty_phrases():
    style = ExtensionStyle.from_dict(valid_style())

    assert compile_prompt_fragment(style) == (
        "ink wash painting, rice-paper texture, expressive brushwork, "
        "restrained ink tones, layered depth, clean silhouettes"
    )


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


def test_load_catalog_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps([valid_style(), valid_style()], ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate.*drama_ext.ink_wash"):
        load_catalog(path)
