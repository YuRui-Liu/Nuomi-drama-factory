#!/usr/bin/env python3
"""审计单集 DOCX：集标题、时长、场次编号、人物行与旧格式残留。

PowerShell 原版 test_single_episode_contract.ps1 的原生 Python 移植。

用法：
    python3 tools/test_single_episode_contract.py --path out/E017.docx \
        --episode-number 17 --episode-title 真正的双面人 --duration-seconds 185
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nuomi_common import (  # noqa: E402
    print_report,
    read_docx_paragraphs,
)

TIME_OF_DAY = "日|夜|清晨|早晨|上午|中午|下午|傍晚|黄昏|深夜"


def main(argv=None):
    parser = argparse.ArgumentParser(description="审计单集 DOCX 交付协议。")
    parser.add_argument("--path", "-p", required=True, help="单集 DOCX 路径")
    parser.add_argument("--episode-number", "-n", type=int, required=True, help="集号")
    parser.add_argument("--episode-title", "-t", required=True, help="集名")
    parser.add_argument("--duration-seconds", "-d", type=int, required=True, help="时长秒数")
    parser.add_argument("--expected-scene-count", type=int, default=3, help="预期场次数量")
    args = parser.parse_args(argv)

    paragraphs = read_docx_paragraphs(args.path)
    failures = []

    expected_title = "第%d集 · %s" % (args.episode_number, args.episode_title)
    expected_duration = "时长：%ds" % args.duration_seconds
    title_matches = [p for p in paragraphs if p["text"] == expected_title]
    duration_matches = [p for p in paragraphs if p["text"] == expected_duration]

    scene_pattern = re.compile(
        r"^%d-\d+\s+.+\s+(%s)\s+(内|外)$" % (args.episode_number, TIME_OF_DAY)
    )
    character_pattern = re.compile(r"^人物：\S+(\s+\S+)+$")
    scene_matches = [
        p
        for p in paragraphs
        if p["style"] == "SceneHeading" and scene_pattern.match(p["text"])
    ]
    character_matches = [
        p
        for p in paragraphs
        if p["style"] == "Characters" and character_pattern.match(p["text"])
    ]
    legacy_titles = [p for p in paragraphs if re.match(r"^第\d+集：", p["text"])]
    content_paragraphs = [p for p in paragraphs if p["text"].strip()]

    if len(title_matches) != 1:
        failures.append(
            "Episode title mismatch: expected exactly 1 '%s', got %d"
            % (expected_title, len(title_matches))
        )
    if len(duration_matches) != 1:
        failures.append(
            "Duration line mismatch: expected exactly 1 '%s', got %d"
            % (expected_duration, len(duration_matches))
        )
    if len(scene_matches) != args.expected_scene_count:
        failures.append(
            "Scene heading mismatch: expected %d numbered headings, got %d"
            % (args.expected_scene_count, len(scene_matches))
        )
    if len(character_matches) != args.expected_scene_count:
        failures.append(
            "Characters line mismatch: expected %d character lines, got %d"
            % (args.expected_scene_count, len(character_matches))
        )
    if legacy_titles:
        failures.append(
            "Legacy episode title format remains: %d paragraph(s)" % len(legacy_titles)
        )
    if (
        len(content_paragraphs) < 2
        or content_paragraphs[0]["text"] != expected_title
        or content_paragraphs[1]["text"] != expected_duration
    ):
        failures.append(
            "Single-episode document must begin with title followed immediately by duration"
        )
    if any(
        p["style"] in ("CoverTitle", "CoverMeta", "SectionTitle") for p in paragraphs
    ):
        failures.append(
            "Single-episode document contains cover or project-preface paragraphs"
        )
    for index, paragraph in enumerate(paragraphs):
        if paragraph["style"] == "SceneHeading":
            if index + 1 >= len(paragraphs) or paragraphs[index + 1]["style"] != "Characters":
                failures.append(
                    "Scene heading at paragraph %d is not followed by a Characters line"
                    % index
                )

    print_report(
        [
            ("Path", os.path.abspath(args.path)),
            ("ParagraphCount", len(paragraphs)),
            ("TitleCount", len(title_matches)),
            ("DurationCount", len(duration_matches)),
            ("SceneHeadingCount", len(scene_matches)),
            ("CharactersCount", len(character_matches)),
            ("LegacyTitleCount", len(legacy_titles)),
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
