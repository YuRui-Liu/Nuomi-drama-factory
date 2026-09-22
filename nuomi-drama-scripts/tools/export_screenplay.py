#!/usr/bin/env python3
"""把 manuscript/episodes/ 编译成交给 Nuomi 漫剧工厂的 deliverables/screenplay.md。

PowerShell 原版 export_screenplay.ps1 的原生 Python 移植。
会剥离 frontmatter 与 HTML 注释，并拒绝任何媒体生成字段。

用法：
    python3 tools/export_screenplay.py \
        --manuscript-dir project-template/manuscript \
        --output-path project-template/deliverables/screenplay.md
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nuomi_common import (  # noqa: E402
    MEDIA_FIELD_PATTERN,
    episode_path,
    fail,
    print_report,
    read_text,
    split_lines,
    write_text,
)


def get_screenplay_body(path):
    """取单集正文：去掉 frontmatter 与 HTML 注释。"""
    lines = split_lines(read_text(path))
    inside_front_matter = False
    inside_comment = False
    body = []
    for raw_line in lines:
        line = raw_line.rstrip()
        if line.strip() == "---" and (not body or inside_front_matter):
            inside_front_matter = not inside_front_matter
            continue
        if inside_front_matter:
            continue
        if line.strip().startswith("<!--"):
            inside_comment = True
        if not inside_comment:
            body.append(line)
        if line.strip().endswith("-->"):
            inside_comment = False

    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        fail("Episode body is empty: %s" % path)
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description="导出 screenplay.md 交接文件。")
    parser.add_argument("--manuscript-dir", "-m", required=True, help="稿件目录")
    parser.add_argument("--output-path", "-o", required=True, help="输出 Markdown 路径")
    parser.add_argument("--episode-start", type=int, default=1, help="起始集号")
    parser.add_argument("--episode-count", type=int, default=30, help="导出集数，1-200")
    args = parser.parse_args(argv)

    if args.episode_start < 1:
        fail("EpisodeStart must be positive.")
    if args.episode_count < 1 or args.episode_count > 200:
        fail("EpisodeCount must be between 1 and 200.")

    resolved_manuscript = os.path.abspath(args.manuscript_dir)
    if not os.path.isdir(resolved_manuscript):
        fail("Manuscript directory does not exist: %s" % resolved_manuscript)

    output_lines = []
    for episode in range(
        args.episode_start, args.episode_start + args.episode_count
    ):
        path = episode_path(resolved_manuscript, episode)
        if not os.path.isfile(path):
            fail("Missing episode source: %s" % path)
        body = get_screenplay_body(path)
        for line in body:
            if MEDIA_FIELD_PATTERN.search(line):
                fail("Media field found in screenplay source: %s" % path)
            output_lines.append(line)
        if episode < args.episode_start + args.episode_count - 1:
            output_lines.extend(["", ""])

    resolved_output = os.path.abspath(args.output_path)
    if os.path.exists(resolved_output):
        fail("Output already exists: %s" % resolved_output)
    write_text(resolved_output, "\n".join(output_lines) + "\n")

    print_report(
        [
            ("Status", "PASS"),
            ("OutputPath", resolved_output),
            ("EpisodeStart", args.episode_start),
            ("EpisodeCount", args.episode_count),
        ]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
