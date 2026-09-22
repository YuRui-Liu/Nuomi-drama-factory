#!/usr/bin/env python3
"""Skill 契约自检：确认 SKILL.md、场景 fixture 与 handoff 模板仍然满足硬性约束。

PowerShell 原版 tests/skill_contract.ps1 的原生 Python 移植。

用法：
    python3 tests/skill_contract.py
"""

import argparse
import os
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "tools"))

from nuomi_common import fail, read_text  # noqa: E402

REQUIRED_SKILL_TOKENS = [
    ("nuomi-drama-scripts", "skill-name"),
    ("brief/bible/beats gate", "batch-gate"),
    ("abstract style features", "style-safety"),
    ("needs_review", "docx-not-ready"),
    ("rights_blocked", "rights-block"),
    ("screenplay.md", "handoff-file"),
    ("does not depend on `nuomi-drama-skills`", "runtime-isolation"),
    ("impact analysis", "fact-change"),
]

REQUIRED_SCENARIO_TOKENS = [
    "batch-gate",
    "style-safety",
    "docx-gate",
    "fact-change",
    "rights-state",
    "runtime-isolation",
    "dramaclaw-handoff",
]

FORBIDDEN_HANDOFF_TOKENS = [
    "image_prompt",
    "video_prompt",
    "provider",
    "model_parameters",
]


def assert_contains(text, token, label):
    if token.lower() not in text.lower():
        fail("[%s] missing: %s" % (label, token))


def assert_not_contains(text, token, label):
    if token.lower() in text.lower():
        fail("[%s] forbidden: %s" % (label, token))


def main(argv=None):
    parser = argparse.ArgumentParser(description="nuomi-drama-scripts 契约自检。")
    parser.add_argument("--skill-root", default=SKILL_ROOT, help="Skill 根目录")
    args = parser.parse_args(argv)

    skill_path = os.path.join(args.skill_root, "SKILL.md")
    scenario_path = os.path.join(args.skill_root, "tests", "fixtures", "skill-scenarios.md")
    handoff_path = os.path.join(args.skill_root, "templates", "screenplay.md")

    skill_text = read_text(skill_path)
    scenario_text = read_text(scenario_path)

    for token, label in REQUIRED_SKILL_TOKENS:
        assert_contains(skill_text, token, label)
    for token in REQUIRED_SCENARIO_TOKENS:
        assert_contains(scenario_text, token, "scenario-fixture")

    if not os.path.isfile(handoff_path):
        fail("[handoff-file] missing template: %s" % handoff_path)
    handoff_text = read_text(handoff_path)
    for token in FORBIDDEN_HANDOFF_TOKENS:
        assert_not_contains(handoff_text, token, "handoff-media-isolation")

    print("PASS: nuomi-drama-scripts skill contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
