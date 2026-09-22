#!/usr/bin/env python3
"""nuomi-drama-scripts 的公共工具库。

从 PowerShell 版本移植而来，仅依赖标准库，可在 macOS / Linux / Windows 上运行。
被 tools/ 下的命令行脚本和 tests/ 下的契约测试共用。
"""

import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS

# build_docx 必须写入的样式 ID，audit_docx 依据同一份清单校验。
STYLE_IDS = [
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

# 分集文件名格式：episodes/E001.md
EPISODE_NAME_FMT = "E%03d.md"

# 禁止出现在剧本正文与 handoff 文件中的媒体生成字段。
MEDIA_FIELD_PATTERN = re.compile(
    r"image_prompt|video_prompt|provider|model_parameters", re.IGNORECASE
)


def fail(message):
    """向 stderr 输出错误并以非零状态退出。"""
    sys.stderr.write("ERROR: %s\n" % message)
    raise SystemExit(1)


def read_text(path):
    """以 UTF-8（无 BOM）读取文本，容忍 CR/LF。"""
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def write_text(path, text):
    """以 UTF-8（无 BOM、LF 换行）写入文本，必要时创建父目录。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def split_lines(text):
    """按 CRLF / LF / CR 切分，等价于 PowerShell 的 -split "\\r?\\n"。"""
    return re.split(r"\r?\n", text)


def get_yaml_scalar(yaml_path, key, default_value=""):
    """从 YAML 顶层键中取标量值。

    注意：PowerShell 原版用 [regex]::Match 且未开 Multiline，导致 `^` 只匹配文件开头，
    该函数实际永远返回默认值。这里按原意修正为多行匹配。
    """
    if not os.path.isfile(yaml_path):
        return default_value
    text = read_text(yaml_path)
    pattern = r"(?m)^[ \t]*" + re.escape(key) + r"\s*:\s*[\"']?(.*?)[\"']?\s*$"
    match = re.search(pattern, text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return default_value


def positive_int_or_none(value):
    """把字符串解析为正整数，失败或 <=0 时返回 None。"""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def episode_path(manuscript_dir, episode_number):
    """分集源文件的跨平台路径。"""
    return os.path.join(manuscript_dir, "episodes", EPISODE_NAME_FMT % episode_number)


def open_docx_zip(path):
    """打开 DOCX 包并校验必需部件。"""
    if not os.path.exists(path):
        fail("DOCX does not exist: %s" % path)
    full_path = os.path.abspath(path)
    try:
        zf = zipfile.ZipFile(full_path, "r")
    except zipfile.BadZipFile:
        fail("Not a valid DOCX (zip) package: %s" % full_path)
    required = [
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/styles.xml",
        "word/settings.xml",
        "word/_rels/document.xml.rels",
    ]
    names = zf.namelist()
    for name in required:
        if name not in names:
            zf.close()
            fail("Missing required entry: %s" % name)
    return full_path, zf


def parse_docx_part(zf, entry_name):
    """解析 DOCX 内的 XML 部件。"""
    try:
        raw = zf.read(entry_name)
    except KeyError:
        fail("Missing package part: %s" % entry_name)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        fail("Malformed XML in %s: %s" % (entry_name, exc))


def read_docx_paragraphs(path):
    """读取 DOCX 正文段落，返回 [{"text":..., "style":...}]。"""
    full_path, zf = open_docx_zip(path)
    try:
        document = parse_docx_part(zf, "word/document.xml")
        body = document.find("%sbody" % W)
        if body is None:
            fail("Missing document body: %s" % full_path)
        results = []
        for paragraph in body.findall("%sp" % W):
            style_node = paragraph.find("./%spPr/%spStyle" % (W, W))
            style = style_node.get("%sval" % W) if style_node is not None else ""
            text = "".join(
                node.text or "" for node in paragraph.iter("%st" % W)
            )
            results.append({"text": text, "style": style or ""})
        return results
    finally:
        zf.close()


def print_report(fields):
    """按 `Key : Value` 对齐输出，替代 PowerShell 的 Format-List。"""
    width = max(len(key) for key, _ in fields)
    for key, value in fields:
        print("%-*s : %s" % (width, key, value))
