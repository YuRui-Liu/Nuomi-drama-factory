#!/usr/bin/env python3
"""检查单集 Markdown 是否满足交付协议。

PowerShell 原版 test_markdown_episode_contract.ps1 的原生 Python 移植。

用法：
    python3 tools/test_markdown_episode_contract.py \
        --path project-template/manuscript/episodes/E001.md --episode-number 1
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nuomi_common import (  # noqa: E402
    MEDIA_FIELD_PATTERN,
    fail,
    print_report,
    read_text,
    split_lines,
)

CJK = "\u4e00-\u9fff"

EPISODE_TITLE_PATTERN = re.compile(r"(?m)^#\s+(E\d{3}|[%s]+\d+\s*集)" % CJK)
DURATION_PATTERN = re.compile(r"(?m)^[%s]+：\d+s\s*$" % CJK)
CHARACTERS_PATTERN = re.compile(r"(?m)^[%s]+：\S+(\s+\S+)+\s*$" % CJK)
ACTION_PATTERN = re.compile(r"(?m)^\u25b3\S*")
DIALOGUE_PATTERN = re.compile(r"(?m)^.+：.+$")


def main(argv=None):
    parser = argparse.ArgumentParser(description="检查单集 Markdown 交付协议。")
    parser.add_argument("--path", "-p", required=True, help="单集 Markdown 路径")
    parser.add_argument("--episode-number", "-n", type=int, required=True, help="集号")
    args = parser.parse_args(argv)

    resolved = os.path.abspath(args.path)
    if not os.path.isfile(resolved):
        fail("Episode source does not exist: %s" % resolved)

    text = read_text(resolved)
    lines = split_lines(text)
    failures = []

    if not EPISODE_TITLE_PATTERN.search(text):
        failures.append("missing episode title")
    if not DURATION_PATTERN.search(text):
        failures.append("missing positive duration")
    scene_heading_pattern = re.compile(
        r"^#{2,3}\s+\S+|^%d-\d+\s+\S+" % args.episode_number
    )
    if not any(scene_heading_pattern.match(line.strip()) for line in lines):
        failures.append("missing scene heading")
    if not CHARACTERS_PATTERN.search(text):
        failures.append("missing characters line")
    if not ACTION_PATTERN.search(text):
        failures.append("missing visible action")
    if not DIALOGUE_PATTERN.search(text):
        failures.append("missing dialogue or sound line")
    if MEDIA_FIELD_PATTERN.search(text):
        failures.append("media field found in episode source")

    print_report(
        [
            ("Path", resolved),
            ("EpisodeNumber", args.episode_number),
            ("Status", "PASS" if not failures else "FAIL"),
            ("FailureCount", len(failures)),
        ]
    )

    if failures:
        for item in failures:
            sys.stderr.write("ERROR: %s\n" % item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
