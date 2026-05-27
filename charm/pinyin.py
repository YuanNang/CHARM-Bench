from __future__ import annotations

import re

from pypinyin import Style, lazy_pinyin

TONE_RE = re.compile(r"[1-5]")


def normalize_pinyin_syllable(value: str) -> str:
    return TONE_RE.sub("", value.strip().lower())


def parse_pinyin_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [normalize_pinyin_syllable(item) for item in value.split(",") if item.strip()]


def pinyin_for_text(value: str) -> list[str]:
    return [
        normalize_pinyin_syllable(item)
        for item in lazy_pinyin(value, style=Style.NORMAL, errors="ignore")
        if item.strip()
    ]
