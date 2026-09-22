#!/usr/bin/env python3
"""审计 DOCX 交付稿的结构、页面参数与排版样式。

PowerShell 原版 audit_docx.ps1 的原生 Python 移植。

用法：
    python3 tools/audit_docx.py --path out/剧本.docx --expected-episodes 30
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nuomi_common import (  # noqa: E402
    W,
    fail,
    open_docx_zip,
    parse_docx_part,
    print_report,
)

EXPECTED_STYLE_IDS = [
    "Normal",
    "CoverTitle",
    "CoverMeta",
    "EpisodeTitle",
    "Duration",
    "SectionTitle",
    "SceneHeading",
    "Characters",
    "Action",
    "Dialogue",
    "Sfx",
]


def paragraph_style(paragraph):
    node = paragraph.find("./%spPr/%spStyle" % (W, W))
    if node is None:
        return ""
    return node.get("%sval" % W) or ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="审计 DOCX 结构与排版。")
    parser.add_argument("--path", "-p", required=True, help="DOCX 路径")
    parser.add_argument("--expected-episodes", type=int, default=30, help="预期集数")
    args = parser.parse_args(argv)

    full_path, zf = open_docx_zip(args.path)
    try:
        document = parse_docx_part(zf, "word/document.xml")
        styles = parse_docx_part(zf, "word/styles.xml")
    finally:
        zf.close()

    section = document.find(".//%ssectPr" % W)
    if section is None:
        fail("Missing sectPr in document.xml")
    page = section.find("%spgSz" % W)
    margin = section.find("%spgMar" % W)
    if page is None:
        fail("Missing pgSz in sectPr")
    if margin is None:
        fail("Missing pgMar in sectPr")

    paragraphs = list(document.iter("%sp" % W))
    counts = {}
    for style_id in EXPECTED_STYLE_IDS:
        counts[style_id] = sum(
            1 for p in paragraphs if paragraph_style(p) == style_id
        )

    present_style_ids = {
        node.get("%sstyleId" % W)
        for node in styles.iter("%sstyle" % W)
    }
    missing_styles = [s for s in EXPECTED_STYLE_IDS if s not in present_style_ids]

    episodes = counts["EpisodeTitle"]
    scenes = counts["SceneHeading"]
    actions = counts["Action"]
    dialogues = counts["Dialogue"]
    sfx = counts["Sfx"]
    characters = counts["Characters"]
    duration = counts["Duration"]

    failures = []
    if page.get("%sw" % W) != "11906" or page.get("%sh" % W) != "16838":
        failures.append("page size is not A4 twips")
    if (
        margin.get("%stop" % W) != "1440"
        or margin.get("%sbottom" % W) != "1440"
        or margin.get("%sleft" % W) != "1800"
        or margin.get("%sright" % W) != "1800"
    ):
        failures.append("margins do not match reference profile")
    if episodes != args.expected_episodes + 1:
        failures.append(
            "EpisodeTitle count is %d, expected %d"
            % (episodes, args.expected_episodes + 1)
        )
    if scenes < args.expected_episodes:
        failures.append(
            "SceneHeading count is %d, expected at least %d"
            % (scenes, args.expected_episodes)
        )
    if actions < args.expected_episodes:
        failures.append("Action paragraphs are missing")
    if dialogues < args.expected_episodes:
        failures.append("Dialogue paragraphs are missing")
    if characters < args.expected_episodes:
        failures.append("Characters paragraphs are missing")
    if sfx < 1:
        failures.append("Sfx paragraph style is missing")
    if missing_styles:
        failures.append("Missing styles: %s" % ", ".join(missing_styles))

    print_report(
        [
            ("Path", full_path),
            ("PageWidthTwips", page.get("%sw" % W)),
            ("PageHeightTwips", page.get("%sh" % W)),
            ("EpisodeTitleCount", episodes),
            ("SceneHeadingCount", scenes),
            ("ActionCount", actions),
            ("DialogueCount", dialogues),
            ("SfxCount", sfx),
            ("DurationCount", duration),
            ("CharactersCount", characters),
            ("MissingStyles", ", ".join(missing_styles)),
            ("Status", "PASS" if not failures else "FAIL"),
        ]
    )

    if failures:
        for item in failures:
            sys.stderr.write("ERROR: %s\n" % item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
