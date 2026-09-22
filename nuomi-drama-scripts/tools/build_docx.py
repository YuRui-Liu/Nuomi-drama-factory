#!/usr/bin/env python3
"""从 manuscript/ 编译 DOCX 剧本交付稿。

PowerShell 原版 build_docx.ps1 的原生 Python 移植，仅依赖标准库。

用法：
    python3 tools/build_docx.py --output-path out/剧本.docx \
        --manuscript-dir project-template/manuscript --episode-count 30

    python3 tools/build_docx.py --output-path out/E017.docx --single-episode \
        --episode-number 17 --manuscript-dir project-template/manuscript

    python3 tools/build_docx.py --output-path out/剧本.docx \
        --manuscript-dir project-template/manuscript --require-delivery-gate
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nuomi_common import (  # noqa: E402
    W_NS,
    episode_path,
    fail,
    get_yaml_scalar,
    positive_int_or_none,
    read_text,
    split_lines,
    write_text,
)

# 角色 -> (字体, 字号 half-points, 颜色, 是否加粗)
RUN_STYLES = {
    "Body": ("SimSun", "21", "000000", False),
    "CoverTitle": ("SimHei", "64", "000000", True),
    "CoverMeta": ("SimSun", "21", "666666", False),
    "EpisodeTitle": ("SimHei", "32", "000000", True),
    "Duration": ("SimSun", "21", "666666", False),
    "SectionTitle": ("SimHei", "24", "6B4A2F", True),
    "SceneHeading": ("SimHei", "23", "2A2A2A", True),
    "Characters": ("SimSun", "21", "555555", False),
    "Action": ("SimSun", "21", "000000", False),
    "Dialogue": ("SimSun", "21", "1F4EC0", False),
    "Sfx": ("SimSun", "21", "C01B1B", False),
}

# 角色 -> (样式 ID, 对齐, 段前, 段后, 行距)
PARAGRAPH_STYLES = {
    "CoverTitle": ("CoverTitle", "center", 0, 240, 480),
    "CoverMeta": ("CoverMeta", "center", 0, 80, 300),
    "EpisodeTitle": ("EpisodeTitle", "center", 240, 240, 360),
    "Duration": ("Duration", "left", 0, 180, 300),
    "SectionTitle": ("SectionTitle", "left", 180, 100, 360),
    "SceneHeading": ("SceneHeading", "left", 160, 80, 300),
    "Characters": ("Characters", "left", 0, 80, 300),
    "Action": ("Action", "left", 0, 100, 360),
    "Dialogue": ("Dialogue", "left", 0, 100, 360),
    "Sfx": ("Sfx", "left", 0, 100, 360),
}

PAGE_BREAK_XML = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'

# 推断人物行时需要排除的非角色前缀
CHARACTER_EXCLUDE = re.compile(r"^(时长|人物|纸条|苏岚字迹|苏岚录音)：")
DIALOGUE_PATTERN = re.compile(r"^[^：:]{1,24}[:：]\s*.+$")
FRONTMATTER_KEY_PATTERN = re.compile(
    r"^(episode|title|volume|status|characters|locations|timeline|"
    r"main_thread_progress|character_thread_progress|"
    r"emotion_or_mystery_progress|hook|body_char_count|rights_risk)\s*:"
)


def escape_xml(value):
    """等价 SecurityElement.Escape：转义 & < > " '。"""
    if value is None:
        return ""
    text = str(value)
    for char, entity in (
        ("&", "&amp;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ('"', "&quot;"),
        ("'", "&apos;"),
    ):
        text = text.replace(char, entity)
    return text


def new_run_xml(text, role="Body", bold=False):
    font, size, color, role_bold = RUN_STYLES.get(role, RUN_STYLES["Body"])
    if bold or role_bold:
        bold_xml = "<w:b/>"
    else:
        bold_xml = ""
    return (
        "<w:r><w:rPr>"
        "<w:rFonts w:ascii='%s' w:eastAsia='宋体' w:hAnsi='%s'/>"
        "<w:color w:val='%s'/>"
        "<w:sz w:val='%s'/><w:szCs w:val='%s'/>%s"
        "</w:rPr><w:t xml:space='preserve'>%s</w:t></w:r>"
    ) % (font, font, color, size, size, bold_xml, escape_xml(text))


def new_paragraph_xml(text, role="Body", page_break_before=False,
                      before=0, after=100, line=360):
    style, alignment = "Normal", "left"
    if role in PARAGRAPH_STYLES:
        style, alignment, before, after, line = PARAGRAPH_STYLES[role]
    break_xml = "<w:pageBreakBefore/>" if page_break_before else ""
    p_pr = (
        "<w:pPr><w:pStyle w:val='%s'/><w:jc w:val='%s'/>"
        "<w:spacing w:before='%d' w:after='%d' w:line='%d' w:lineRule='auto'/>%s</w:pPr>"
    ) % (style, alignment, before, after, line, break_xml)
    return "<w:p>%s%s</w:p>" % (p_pr, new_run_xml(text, role))


def infer_characters(lines, start_index):
    """场景标题后缺少人物行时，从后续对白里推断角色名。"""
    names = []
    for scan_index in range(start_index, len(lines)):
        candidate = lines[scan_index].strip()
        if re.match(r"^#{1,3}\s", candidate):
            break
        match = re.match(r"^([^：:]{1,24})[:：]\s*.+$", candidate)
        if not match:
            continue
        name = match.group(1).strip()
        if CHARACTER_EXCLUDE.match(candidate):
            continue
        if name not in names:
            names.append(name)
    return names


def add_markdown_file_to_document(paragraphs, file_path,
                                  skip_episode_heading=False, episode_mode=False):
    """把 Markdown 稿件转成 DOCX 段落 XML。"""
    if not os.path.isfile(file_path):
        return
    lines = split_lines(read_text(file_path))
    front_matter = False
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line == "---":
            front_matter = not front_matter
            continue
        if front_matter or not line:
            continue
        if episode_mode and line.startswith("时长："):
            continue
        if skip_episode_heading and re.match(r"^#\s*(第\d+\s*集|E\d{3})", line):
            continue

        heading_match = re.match(r"^#{1,3}\s*(.+)$", line)
        if heading_match:
            heading = heading_match.group(1).strip()
            is_scene = (
                (episode_mode and re.match(r"^###\s", line))
                or re.match(r"^\d+-\d+\s+", heading)
                or re.search(r"·[^·]+·", heading)
                or re.match(r"^(INT|EXT)[\.、\s]", heading)
            )
            if is_scene:
                paragraphs.append(new_paragraph_xml(heading, "SceneHeading"))
                if episode_mode:
                    next_non_empty = ""
                    for scan_index in range(index + 1, len(lines)):
                        candidate = lines[scan_index].strip()
                        if candidate:
                            next_non_empty = candidate
                            break
                    if not next_non_empty.startswith("人物："):
                        names = infer_characters(lines, index + 1)
                        if names:
                            paragraphs.append(
                                new_paragraph_xml("人物：" + " ".join(names), "Characters")
                            )
            else:
                paragraphs.append(new_paragraph_xml(heading, "SectionTitle"))
            continue

        if line.startswith("△"):
            paragraphs.append(new_paragraph_xml(line, "Action"))
            continue
        if line.startswith("【"):
            paragraphs.append(new_paragraph_xml(line, "Sfx"))
            continue
        if episode_mode and re.match(r"^人物：\S+(\s+\S+)+$", line):
            paragraphs.append(new_paragraph_xml(line, "Characters"))
            continue
        if DIALOGUE_PATTERN.match(line):
            paragraphs.append(new_paragraph_xml(line, "Dialogue"))
            continue
        if FRONTMATTER_KEY_PATTERN.match(line):
            continue
        paragraphs.append(new_paragraph_xml(line, "Action"))


def new_styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="%s">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:ascii="SimSun" w:eastAsia="宋体" w:hAnsi="SimSun"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:ascii="SimSun" w:eastAsia="宋体" w:hAnsi="SimSun"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="CoverTitle"><w:name w:val="CoverTitle"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="1"/><w:qFormat/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:sz w:val="64"/><w:szCs w:val="64"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="CoverMeta"><w:name w:val="CoverMeta"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:color w:val="666666"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="EpisodeTitle"><w:name w:val="EpisodeTitle"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Duration"><w:name w:val="Duration"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="666666"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="SectionTitle"><w:name w:val="SectionTitle"/><w:basedOn w:val="Normal"/><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:color w:val="6B4A2F"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="SceneHeading"><w:name w:val="SceneHeading"/><w:basedOn w:val="Normal"/><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:color w:val="2A2A2A"/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Characters"><w:name w:val="Characters"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="555555"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Action"><w:name w:val="Action"/><w:basedOn w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Dialogue"><w:name w:val="Dialogue"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="1F4EC0"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Sfx"><w:name w:val="Sfx"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="C01B1B"/></w:rPr></w:style>
</w:styles>""" % W_NS


def new_document_xml(name, count, manuscript_dir="", start_episode=1,
                     default_duration_seconds=90, single_episode=False):
    paragraphs = []
    if not single_episode:
        paragraphs.append(new_paragraph_xml(name, "CoverTitle"))
        paragraphs.append(new_paragraph_xml("女频 · 现实共鸣 · 格式验收样稿", "CoverMeta"))
        paragraphs.append(
            new_paragraph_xml("首交 %d 集 · 红果漫剧 DOCX 结构验收" % count, "CoverMeta")
        )
        paragraphs.append(new_paragraph_xml("A4 · 9:16 竖屏内容的剧本交付格式画像", "CoverMeta"))
        paragraphs.append(new_paragraph_xml("版本：v0.1 · 内容为格式测试样例，不是投稿正文", "CoverMeta"))
        paragraphs.append(new_paragraph_xml("交付状态：delivery_blocked（待渲染环境恢复）", "CoverMeta"))
        paragraphs.append(PAGE_BREAK_XML)

        paragraphs.append(new_paragraph_xml("第零集", "EpisodeTitle"))
        paragraphs.append(new_paragraph_xml("——《人物小传与故事大纲》——", "SectionTitle"))
        if not manuscript_dir:
            paragraphs.append(new_paragraph_xml("一、格式验收说明", "SectionTitle"))
            paragraphs.append(
                new_paragraph_xml(
                    "本页用于检查封面后的前置结构。真实项目将在此处放置人物小传、故事简介、故事大纲和版权状态。",
                    "Action",
                )
            )
            paragraphs.append(new_paragraph_xml("二、人物小传（测试）", "SectionTitle"))
            paragraphs.append(
                new_paragraph_xml("主角：待真实项目填写。当前内容仅用于验证正文段落、字体和分页。", "Action")
            )
            paragraphs.append(new_paragraph_xml("三、故事大纲（测试）", "SectionTitle"))
            paragraphs.append(new_paragraph_xml("故事大纲：待真实项目填写。当前内容不能作为投稿内容。", "Action"))
        else:
            paragraphs.append(new_paragraph_xml("一、项目简报", "SectionTitle"))
            add_markdown_file_to_document(
                paragraphs, os.path.join(manuscript_dir, "00_项目简报.md")
            )
            paragraphs.append(new_paragraph_xml("二、人物小传与剧本圣经", "SectionTitle"))
            add_markdown_file_to_document(
                paragraphs, os.path.join(manuscript_dir, "04_人物表.md")
            )
            add_markdown_file_to_document(
                paragraphs, os.path.join(manuscript_dir, "03_剧本圣经.md")
            )
            paragraphs.append(new_paragraph_xml("三、故事简介与大纲", "SectionTitle"))
            add_markdown_file_to_document(
                paragraphs, os.path.join(manuscript_dir, "02_故事大纲.md")
            )

    for episode in range(start_episode, start_episode + count):
        if manuscript_dir:
            path = episode_path(manuscript_dir, episode)
            episode_title = get_yaml_scalar(path, "title", "第%d 集" % episode)
            duration_seconds = default_duration_seconds
            parsed = positive_int_or_none(get_yaml_scalar(path, "duration_seconds", ""))
            if parsed:
                duration_seconds = parsed
            title = "第%d集 · %s" % (episode, episode_title)
        else:
            duration_seconds = default_duration_seconds
            title = "第%d集 · 格式验收样例" % episode

        paragraphs.append(
            new_paragraph_xml(
                title, "EpisodeTitle", page_break_before=not single_episode
            )
        )
        paragraphs.append(new_paragraph_xml("时长：%ds" % duration_seconds, "Duration"))

        if manuscript_dir:
            add_markdown_file_to_document(
                paragraphs, path, skip_episode_heading=True, episode_mode=True
            )
        else:
            paragraphs.append(new_paragraph_xml("%d-1 测试场景（内） 日" % episode, "SceneHeading"))
            paragraphs.append(new_paragraph_xml("人物：主角", "Characters"))
            paragraphs.append(
                new_paragraph_xml(
                    "△ 这是格式验收动作行：检查场景标题、动作段落和段间距。真实项目将由 manuscript/episodes/ 文件生成。",
                    "Action",
                )
            )
            paragraphs.append(
                new_paragraph_xml("△ 主角拿起一件登记在剧本圣经中的道具，冲突在本场景中产生具体推进。", "Action")
            )
            paragraphs.append(
                new_paragraph_xml("主角：（测试对白）这句文字用于检查对白颜色、角色名和中文字体。", "Dialogue")
            )
            paragraphs.append(new_paragraph_xml("【音效】门锁轻响。", "Sfx"))
            paragraphs.append(
                new_paragraph_xml(
                    "△ 第%d 集结尾形成测试钩子；真实项目必须替换为具体的不可逆事件。" % episode,
                    "Action",
                )
            )

    body = "".join(paragraphs)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<w:document xmlns:w="%s" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            % W_NS,
            "  <w:body>",
            body,
            "    <w:sectPr>",
            '      <w:pgSz w:w="11906" w:h="16838"/>',
            '      <w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" '
            'w:header="720" w:footer="720" w:gutter="0"/>',
            '      <w:cols w:num="1"/>',
            '      <w:docGrid w:linePitch="360"/>',
            "    </w:sectPr>",
            "  </w:body>",
            "</w:document>",
        ]
    )


def new_settings_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="%s">
  <w:updateFields w:val="true"/>
  <w:compat/>
</w:settings>""" % W_NS


def new_content_types_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>"""


def new_root_relationships_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def new_document_relationships_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>"""


BLOCKED_STATUSES = [
    "pending",
    "blocked",
    "rights_blocked",
    "needs_review",
    "draft",
    "draft-for-platform-test",
    "blocked-by-render-environment",
]

RIGHTS_KEYS = (
    "original_or_authorized|adaptation_license|"
    "image_voice_brand_clearance|ai_assistance_disclosure"
)


def assert_delivery_gate(resolved_manuscript_dir):
    """交付闸门：项目配置、审查记录、渲染 QA 齐备且无阻断状态。"""
    project_root = os.path.dirname(resolved_manuscript_dir)
    project_yaml = os.path.join(project_root, "project.yaml")
    reviews_dir = os.path.join(project_root, "reviews")
    qa_dir = os.path.join(project_root, "deliverables", "qa")

    if not os.path.isfile(project_yaml):
        fail("Delivery gate blocked: project.yaml is missing.")
    if not os.path.isdir(reviews_dir):
        fail("Delivery gate blocked: reviews directory is missing.")
    if not os.path.isdir(qa_dir):
        fail("Delivery gate blocked: rendered QA directory is missing.")
    if not any(
        os.path.isfile(os.path.join(root, name))
        for root, _dirs, files in os.walk(qa_dir)
        for name in files
    ):
        fail("Delivery gate blocked: rendered QA evidence is missing.")

    yaml_text = read_text(project_yaml)
    for status in BLOCKED_STATUSES:
        rights_pattern = (
            r"(?im)^\s*(%s)\s*:\s*[\"']?%s[\"']?\s*$"
            % (RIGHTS_KEYS, re.escape(status))
        )
        if re.search(rights_pattern, yaml_text):
            fail("Delivery gate blocked: project rights/AI status is %s." % status)
        delivery_pattern = (
            r"(?im)^\s*(status|visual_qa)\s*:\s*[\"']?%s[\"']?\s*$"
            % re.escape(status)
        )
        if re.search(delivery_pattern, yaml_text):
            fail("Delivery gate blocked: delivery status is %s." % status)

    review_pattern = (
        r"(?im)^\s*(status|delivery_status|rights_status)\s*:\s*"
        r"(P0|P1|blocked|rights_blocked|needs_review|pending)\s*$"
    )
    for root, _dirs, files in os.walk(reviews_dir):
        for name in files:
            review_file = os.path.join(root, name)
            try:
                text = read_text(review_file)
            except (UnicodeDecodeError, OSError):
                continue
            if re.search(review_pattern, text):
                fail("Delivery gate blocked by review: %s" % review_file)


def build_parser():
    parser = argparse.ArgumentParser(
        description="从 manuscript/ 编译 DOCX 剧本交付稿。"
    )
    parser.add_argument("--output-path", "-o", required=True, help="输出 DOCX 路径")
    parser.add_argument("--episode-count", type=int, default=30, help="集数，1-200")
    parser.add_argument("--episode-number", type=int, default=0, help="单集导出时的集号")
    parser.add_argument("--project-name", default="格式验收稿", help="封面剧名")
    parser.add_argument("--smoke-test", action="store_true", help="标记为冒烟测试构建")
    parser.add_argument("--single-episode", action="store_true", help="只导出一集")
    parser.add_argument(
        "--manuscript-dir", "-m", default="", help="稿件目录，含 episodes/"
    )
    parser.add_argument(
        "--require-delivery-gate", action="store_true", help="编译前执行完整交付闸门校验"
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.episode_count < 1 or args.episode_count > 200:
        fail("EpisodeCount must be between 1 and 200.")
    if args.single_episode and (args.episode_number < 1 or args.episode_number > 200):
        fail("SingleEpisode requires EpisodeNumber between 1 and 200.")
    if not args.single_episode and args.episode_number != 0:
        fail("EpisodeNumber can only be used with SingleEpisode.")
    if args.single_episode:
        args.episode_count = 1

    resolved_output = os.path.abspath(args.output_path)
    parent = os.path.dirname(resolved_output)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if os.path.exists(resolved_output):
        fail("Output already exists: %s" % resolved_output)

    start_episode = args.episode_number if args.single_episode else 1
    default_duration_seconds = 90
    resolved_manuscript_dir = ""

    if args.manuscript_dir.strip():
        resolved_manuscript_dir = os.path.abspath(args.manuscript_dir)
        if not os.path.isdir(resolved_manuscript_dir):
            fail("Manuscript directory does not exist: %s" % resolved_manuscript_dir)
        for episode in range(start_episode, start_episode + args.episode_count):
            path = episode_path(resolved_manuscript_dir, episode)
            if not os.path.isfile(path):
                fail("Missing episode source: %s" % path)
            if args.single_episode:
                duration = positive_int_or_none(
                    get_yaml_scalar(path, "duration_seconds", "")
                )
                if not duration:
                    fail(
                        "Single episode source must declare a positive "
                        "duration_seconds: %s" % path
                    )
        if args.project_name == "格式验收稿":
            project_yaml = os.path.join(resolved_manuscript_dir, "..", "project.yaml")
            project_yaml = os.path.normpath(project_yaml)
            name = get_yaml_scalar(project_yaml, "project_name", "")
            if not name:
                name = get_yaml_scalar(project_yaml, "title", "格式验收稿")
            args.project_name = name
            # 原版只读 episode_duration_seconds，与模板里的
            # episode_duration_default_seconds 对不上；这里补上兜底。
            project_duration = positive_int_or_none(
                get_yaml_scalar(project_yaml, "episode_duration_seconds", "")
                or get_yaml_scalar(project_yaml, "episode_duration_default_seconds", "")
            )
            if project_duration:
                default_duration_seconds = project_duration

    if args.require_delivery_gate:
        if not resolved_manuscript_dir:
            fail("RequireDeliveryGate requires ManuscriptDir.")
        assert_delivery_gate(resolved_manuscript_dir)

    staging = tempfile.mkdtemp(prefix="nuomi-script-docx-")
    try:
        write_text(os.path.join(staging, "[Content_Types].xml"), new_content_types_xml())
        write_text(os.path.join(staging, "_rels", ".rels"), new_root_relationships_xml())
        write_text(
            os.path.join(staging, "word", "document.xml"),
            new_document_xml(
                name=args.project_name,
                count=args.episode_count,
                manuscript_dir=resolved_manuscript_dir,
                start_episode=start_episode,
                default_duration_seconds=default_duration_seconds,
                single_episode=args.single_episode,
            ),
        )
        write_text(os.path.join(staging, "word", "styles.xml"), new_styles_xml())
        write_text(os.path.join(staging, "word", "settings.xml"), new_settings_xml())
        write_text(
            os.path.join(staging, "word", "_rels", "document.xml.rels"),
            new_document_relationships_xml(),
        )

        with zipfile.ZipFile(resolved_output, "w", zipfile.ZIP_DEFLATED) as zf:
            # [Content_Types].xml 必须第一个写入
            for arc_name in (
                "[Content_Types].xml",
                "_rels/.rels",
                "word/document.xml",
                "word/styles.xml",
                "word/settings.xml",
                "word/_rels/document.xml.rels",
            ):
                zf.write(os.path.join(staging, *arc_name.split("/")), arc_name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print("Created %s" % resolved_output)
    if args.require_delivery_gate:
        print("Status ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
