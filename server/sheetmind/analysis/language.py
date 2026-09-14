"""Response-language policy for user-generated analysis content."""
from __future__ import annotations

import re
from typing import Any, Literal, Optional


ResponseLanguage = Literal["en", "zh"]

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
_ENGLISH_REQUEST_RE = re.compile(
    r"(?:用|使用)英文(?:回答|回复|输出)?|(?:answer|reply|respond|write)\s+in\s+english",
    re.IGNORECASE,
)
_CHINESE_REQUEST_RE = re.compile(
    r"(?:用|使用)中文(?:回答|回复|输出)?|(?:answer|reply|respond|write)\s+in\s+chinese",
    re.IGNORECASE,
)


def infer_response_language(query: str) -> ResponseLanguage:
    """Infer the language of the latest query while tolerating raw identifiers."""
    text = str(query or "").strip()
    if _ENGLISH_REQUEST_RE.search(text):
        return "en"
    if _CHINESE_REQUEST_RE.search(text):
        return "zh"

    cjk_count = len(_CJK_RE.findall(text))
    latin_word_count = len(_LATIN_WORD_RE.findall(text))
    if cjk_count == 0:
        return "en"
    if latin_word_count == 0:
        return "zh"

    # Chinese questions often contain English field names such as SKU or cost.
    # Compare CJK characters with words, rather than Latin characters, so those
    # identifiers do not incorrectly switch the whole response to English.
    return "zh" if cjk_count >= max(2, latin_word_count * 2) else "en"


def resolve_response_language(
    query: str,
    planned_language: Optional[Any] = None,
) -> ResponseLanguage:
    """Validate a planner decision, with explicit user requests taking priority."""
    text = str(query or "")
    if _ENGLISH_REQUEST_RE.search(text):
        return "en"
    if _CHINESE_REQUEST_RE.search(text):
        return "zh"
    if planned_language in {"en", "zh"}:
        return planned_language
    return infer_response_language(text)


def language_name(language: ResponseLanguage) -> str:
    return "Simplified Chinese" if language == "zh" else "English"


def user_text(language: ResponseLanguage, *, en: str, zh: str) -> str:
    return zh if language == "zh" else en


def quota_exhausted_message(language: ResponseLanguage) -> str:
    return user_text(
        language,
        en="The API quota has been exhausted. Please try again later.",
        zh="API 额度已耗尽，请稍后再试。",
    )
