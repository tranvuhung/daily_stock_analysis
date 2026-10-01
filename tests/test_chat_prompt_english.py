# -*- coding: utf-8 -*-
"""Prompt hệ thống chat + hướng dẫn chiến lược dựng sẵn dùng tiếng Anh làm chuẩn (không chữ Hán)."""

import os
import re
import sys

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.executor import (  # noqa: E402
    CHAT_SYSTEM_PROMPT,
    CODEX_CHAT_SYSTEM_PROMPT,
    LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT,
    _build_language_section,
)
from src.agent.skills.base import SkillManager  # noqa: E402
from src.agent.skills.defaults import get_default_trading_skill_policy  # noqa: E402
from src.market_context import get_market_guidelines, get_market_role  # noqa: E402

_HAN = re.compile(r"[㐀-䶿一-鿿]")
_STRATEGY_DIR = os.path.join(os.path.dirname(__file__), "..", "strategies")


def _all_skill_instructions() -> str:
    manager = SkillManager()
    manager.load_builtin_skills()
    manager.activate([skill.name for skill in manager.list_skills()])
    return manager.get_skill_instructions()


@pytest.mark.parametrize("template", [CHAT_SYSTEM_PROMPT, LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT])
def test_vietnamese_chat_prompt_with_all_builtin_skills_has_no_han(template: str) -> None:
    prompt = template.format(
        market_role=get_market_role("LAS", "vi"),
        market_guidelines=get_market_guidelines("LAS", "vi"),
        default_skill_policy_section="\n" + get_default_trading_skill_policy(explicit_skill_selection=False) + "\n",
        skills_section="## Active trading skills\n\n" + _all_skill_instructions(),
        language_section=_build_language_section("vi", chat_mode=True),
    )

    assert "### Skill 1:" in prompt
    assert not _HAN.search(prompt), [line for line in prompt.splitlines() if _HAN.search(line)][:3]


def test_codex_chat_prompt_has_no_han() -> None:
    prompt = CODEX_CHAT_SYSTEM_PROMPT.format(
        market_role=get_market_role("LAS", "vi"),
        language_section=_build_language_section("vi", chat_mode=True),
    )
    assert not _HAN.search(prompt)


@pytest.mark.parametrize("filename", sorted(f for f in os.listdir(_STRATEGY_DIR) if f.endswith(".yaml")))
def test_builtin_strategy_yaml_is_english_except_ui_names(filename: str) -> None:
    path = os.path.join(_STRATEGY_DIR, filename)
    data = yaml.safe_load(open(path, encoding="utf-8"))

    # display_name / aliases giữ tiếng Trung: Web, bot và báo cáo dùng làm khoá đối chiếu.
    assert data["display_name_en"] and not _HAN.search(data["display_name_en"])
    assert not _HAN.search(data["description"])
    assert not _HAN.search(data["instructions"])
