"""Deterministic resolution of query field mentions to dataframe columns."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .semantic_typing import SemanticFieldInfo, SemanticFieldMap


@dataclass(frozen=True)
class FieldResolution:
    column: str
    score: float
    reason: str


def normalise_field_text(value: str) -> str:
    text = str(value).lower()
    replacements = {"人民币": "rmb", "cny": "rmb", "美元": "usd", "¥": "rmb", "$": "usd"}
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"[\s_\-（(）)【】\[\]{},，。:：]", "", text)


def query_qualifiers(query: str) -> set[str]:
    normalised = normalise_field_text(query)
    qualifiers = set()
    for qualifier, signals in {
        "rmb": ("rmb",), "usd": ("usd",), "tax_included": ("含税", "价税合计"),
        "tax_excluded": ("不含税", "未税"), "original_currency": ("原币",),
        "local_currency": ("本币",), "percent": ("百分比", "%", "率"),
    }.items():
        if any(normalise_field_text(signal) in normalised for signal in signals):
            qualifiers.add(qualifier)
    return qualifiers


class FieldResolver:
    """Score aliases and qualifiers, with qualified metric matches taking priority."""

    def resolve(
        self,
        query: str,
        field_map: SemanticFieldMap,
        *,
        allowed_types: Optional[Iterable[str]] = None,
        aggregate_only: bool = False,
    ) -> Optional[FieldResolution]:
        matches = self.rank(query, field_map, allowed_types=allowed_types, aggregate_only=aggregate_only)
        return matches[0] if matches else None

    def rank(
        self,
        query: str,
        field_map: SemanticFieldMap,
        *,
        allowed_types: Optional[Iterable[str]] = None,
        aggregate_only: bool = False,
    ) -> list[FieldResolution]:
        allowed = set(allowed_types or [])
        q = normalise_field_text(query)
        q_qualifiers = query_qualifiers(query)
        ranked: list[FieldResolution] = []

        for column, info in field_map.items():
            if allowed and info.type not in allowed:
                continue
            if aggregate_only and not info.should_aggregate:
                continue

            aliases = {normalise_field_text(alias) for alias in [column, info.canonical_name, *info.aliases]}
            score = 0.0
            reasons: list[str] = []
            for alias in aliases:
                if not alias:
                    continue
                if alias == q:
                    score = max(score, 100.0 + len(alias))
                    reasons.append("exact alias")
                elif alias in q and len(alias) >= 2:
                    score = max(score, 30.0 + len(alias) * 3)
                    reasons.append("query alias")
                elif q in alias and len(q) >= 2:
                    score = max(score, 15.0 + len(q) * 2)
                    reasons.append("column alias")

            if q_qualifiers:
                overlap = q_qualifiers.intersection(info.qualifiers)
                if overlap:
                    score += 80.0 * len(overlap)
                    reasons.append("qualified match")
                elif info.qualifiers:
                    score -= 25.0
                else:
                    score -= 55.0

            if info.type == "numeric" and aggregate_only:
                score += 5.0
            if score > 0:
                ranked.append(FieldResolution(column=column, score=score, reason=", ".join(reasons) or "semantic match"))

        return sorted(ranked, key=lambda item: (-item.score, item.column))
