"""Deterministic query normalization shared by planning and execution."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from ..context import AnalysisContext
from .base import Skill


class NormalizedNumber(BaseModel):
    raw: str
    value: int


class NormalizedTimeRange(BaseModel):
    raw: str
    start: str
    end: str
    granularity: str


class NormalizedQuery(BaseModel):
    """Original text plus a deterministic representation for machine matching."""

    original_text: str
    normalized_text: str
    numbers: List[NormalizedNumber] = Field(default_factory=list)
    time_ranges: List[NormalizedTimeRange] = Field(default_factory=list)


_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CHINESE_NUMBER = "零〇一二两三四五六七八九十百千万"
_CONTEXTUAL_NUMBER_RE = re.compile(
    rf"(?P<prefix>前|近|过去|最近|第|超过|大于|小于|不少于|不超过)"
    rf"\s*(?P<number>[{_CHINESE_NUMBER}]+)"
    r"(?P<suffix>个月|季度|个|名|条|天|周|月|年|%)"
)
_CALENDAR_NUMBER_RE = re.compile(
    rf"(?P<number>[{_CHINESE_NUMBER}]+)(?P<suffix>年份|年度|月份|月|年)"
)
_PUNCTUATION_TRANSLATION = str.maketrans({
    "，": ",", "。": ".", "；": ";", "：": ":", "！": "!", "？": "?",
})


def _chinese_number_to_int(text: str) -> int:
    if not any(char in _UNITS for char in text):
        return int("".join(str(_DIGITS[char]) for char in text))

    total = 0
    section = 0
    number = 0
    for char in text:
        if char in _DIGITS:
            number = _DIGITS[char]
            continue
        unit = _UNITS[char]
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            section += (number or 1) * unit
            number = 0
    return total + section + number


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


class QueryNormalizationSkill(Skill):
    """Normalize matching syntax without using an LLM or mutating user text."""

    name = "query_normalization"
    description = "Normalize query numbers, relative dates, whitespace, and punctuation"

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        reference_date: Optional[date] = None,
        **kwargs: Any,
    ) -> NormalizedQuery:
        original = str(query).strip()
        normalized = unicodedata.normalize("NFKC", original).translate(
            _PUNCTUATION_TRANSLATION
        )
        normalized = re.sub(r"\s+", " ", normalized).strip()
        numbers: List[NormalizedNumber] = []

        def replace_number(match: re.Match[str]) -> str:
            raw = match.group("number")
            value = _chinese_number_to_int(raw)
            numbers.append(NormalizedNumber(raw=raw, value=value))
            return f"{match.group('prefix')}{value}{match.group('suffix')}"

        normalized = _CONTEXTUAL_NUMBER_RE.sub(replace_number, normalized)

        def replace_calendar_number(match: re.Match[str]) -> str:
            raw = match.group("number")
            value = _chinese_number_to_int(raw)
            numbers.append(NormalizedNumber(raw=raw, value=value))
            return f"{value}{match.group('suffix')}"

        normalized = _CALENDAR_NUMBER_RE.sub(replace_calendar_number, normalized)

        anchor = reference_date or datetime.now().astimezone().date()
        normalized, time_ranges = self._normalize_time_ranges(normalized, anchor)
        return NormalizedQuery(
            original_text=original,
            normalized_text=normalized,
            numbers=numbers,
            time_ranges=time_ranges,
        )

    @staticmethod
    def _normalize_time_ranges(
        text: str,
        anchor: date,
    ) -> tuple[str, List[NormalizedTimeRange]]:
        ranges: List[NormalizedTimeRange] = []
        this_month = _month_start(anchor)
        this_week = anchor - timedelta(days=anchor.weekday())
        fixed = {
            "今天": (anchor, anchor + timedelta(days=1), "day"),
            "昨天": (anchor - timedelta(days=1), anchor, "day"),
            "本周": (this_week, this_week + timedelta(days=7), "week"),
            "上周": (this_week - timedelta(days=7), this_week, "week"),
            "本月": (this_month, _shift_months(this_month, 1), "month"),
            "这个月": (this_month, _shift_months(this_month, 1), "month"),
            "上个月": (_shift_months(this_month, -1), this_month, "month"),
            "今年": (date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1), "year"),
            "去年": (date(anchor.year - 1, 1, 1), date(anchor.year, 1, 1), "year"),
        }

        for raw in sorted(fixed, key=len, reverse=True):
            if raw not in text:
                continue
            start, end, granularity = fixed[raw]
            ranges.append(NormalizedTimeRange(
                raw=raw,
                start=start.isoformat(),
                end=end.isoformat(),
                granularity=granularity,
            ))
            text = text.replace(raw, f"{start.isoformat()}至{end.isoformat()}")

        rolling_month_re = re.compile(r"(?:近|过去|最近)(\d+)个月")
        for match in list(rolling_month_re.finditer(text)):
            count = max(1, int(match.group(1)))
            end = _shift_months(this_month, 1)
            start = _shift_months(end, -count)
            raw = match.group(0)
            ranges.append(NormalizedTimeRange(
                raw=raw,
                start=start.isoformat(),
                end=end.isoformat(),
                granularity="month",
            ))
            text = text.replace(raw, f"{start.isoformat()}至{end.isoformat()}", 1)

        return text, ranges
