"""Deterministic resolution of query field mentions to dataframe columns."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

from .semantic_typing import SemanticFieldInfo, SemanticFieldMap


@dataclass(frozen=True)
class FieldResolution:
    column: str
    score: float
    reason: str
    confidence: float = 0.0
    matched_alias: str = ""
    match_kind: str = "semantic"


@dataclass(frozen=True)
class FieldResolutionDecision:
    reference: str
    status: Literal["confirmed", "assumed", "needs_clarification"]
    selected: Optional[FieldResolution]
    candidates: list[FieldResolution]
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

    def decide_all(
        self,
        query: str,
        field_map: SemanticFieldMap,
        *,
        mentions: Optional[Iterable[str]] = None,
    ) -> list[FieldResolutionDecision]:
        """Resolve each field concept mentioned by the query independently."""
        supplied_mentions = [str(value) for value in (mentions or []) if str(value).strip()]
        q = normalise_field_text(query)
        groups: dict[tuple[str, str], SemanticFieldMap] = {}
        for column, info in field_map.items():
            key = (info.semantic_role, normalise_field_text(info.canonical_name or column))
            groups.setdefault(key, {})[column] = info

        decisions: list[FieldResolutionDecision] = []
        for fields in groups.values():
            aliases = self._group_aliases(fields)
            matching_mentions = [
                mention
                for mention in supplied_mentions
                if self._mention_matches_group(mention, aliases)
            ]
            query_aliases = [alias for alias in aliases if len(alias) >= 2 and alias in q]
            if not matching_mentions and not query_aliases:
                continue

            reference = max(
                [*matching_mentions, *query_aliases],
                key=lambda value: len(normalise_field_text(value)),
            )
            decision = self._decide_group(fields, reference)
            if decision is not None:
                decisions.append(decision)

        return decisions

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

            aliases = list(dict.fromkeys(
                normalise_field_text(alias)
                for alias in [column, info.canonical_name, *info.aliases]
                if normalise_field_text(alias)
            ))
            score = 0.0
            reasons: list[str] = []
            matched_alias = ""
            match_kind = "semantic"
            for alias in aliases:
                if not alias:
                    continue
                if alias == q:
                    candidate_score = 100.0 + len(alias)
                    if candidate_score > score:
                        score = candidate_score
                        matched_alias = alias
                        match_kind = "exact"
                    reasons.append("与字段别名完全匹配")
                elif alias in q and len(alias) >= 2:
                    candidate_score = 30.0 + len(alias) * 3
                    if candidate_score > score:
                        score = candidate_score
                        matched_alias = alias
                        match_kind = "query_alias"
                    reasons.append("字段名称出现在查询中")
                elif q in alias and len(q) >= 2:
                    candidate_score = 15.0 + len(q) * 2
                    if candidate_score > score:
                        score = candidate_score
                        matched_alias = q
                        match_kind = "partial"
                    reasons.append("查询词与字段名称部分匹配")

            if q_qualifiers and (info.semantic_role == "metric" or info.qualifiers):
                overlap = q_qualifiers.intersection(info.qualifiers)
                if overlap:
                    score += 80.0 * len(overlap)
                    reasons.append("币种或单位限定词匹配")
                elif info.qualifiers:
                    score -= 25.0
                else:
                    score -= 55.0

            if score > 0:
                if info.type == "numeric" and aggregate_only:
                    score += 5.0
                ranked.append(FieldResolution(
                    column=column,
                    score=score,
                    reason="，".join(dict.fromkeys(reasons)) or "语义匹配",
                    confidence=self._score_confidence(score, match_kind),
                    matched_alias=matched_alias,
                    match_kind=match_kind,
                ))

        return sorted(ranked, key=lambda item: (-item.score, item.column))

    def _decide_group(
        self,
        fields: SemanticFieldMap,
        reference: str,
    ) -> Optional[FieldResolutionDecision]:
        candidates = self.rank(reference, fields)[:5]
        if not candidates:
            return None
        top = candidates[0]
        if len(candidates) == 1:
            if top.match_kind in {"exact", "query_alias"}:
                return FieldResolutionDecision(
                    reference=reference,
                    status="confirmed",
                    selected=top,
                    candidates=candidates,
                    reason=top.reason,
                )
            return FieldResolutionDecision(
                reference=reference,
                status="assumed",
                selected=top,
                candidates=candidates,
                reason=f"仅找到一个部分匹配字段：{top.column}",
            )

        margin = top.score - candidates[1].score
        if margin >= 40.0:
            return FieldResolutionDecision(
                reference=reference,
                status="confirmed",
                selected=top,
                candidates=candidates,
                reason=f"{top.reason}，且明显优于其他候选",
            )
        if margin >= 15.0:
            return FieldResolutionDecision(
                reference=reference,
                status="assumed",
                selected=top,
                candidates=candidates,
                reason=f"{top.reason}，但仍存在相似字段",
            )
        return FieldResolutionDecision(
            reference=reference,
            status="needs_clarification",
            selected=None,
            candidates=candidates,
            reason="多个候选字段的匹配程度接近，自动选择可能改变分析结果",
        )

    @staticmethod
    def _group_aliases(fields: SemanticFieldMap) -> list[str]:
        aliases: list[str] = []
        for column, info in fields.items():
            for alias in [column, info.canonical_name, *info.aliases]:
                normalized = normalise_field_text(alias)
                if normalized and normalized not in aliases:
                    aliases.append(normalized)
        return aliases

    @staticmethod
    def _mention_matches_group(mention: str, aliases: Iterable[str]) -> bool:
        normalized = normalise_field_text(mention)
        if not normalized:
            return False
        normalized_aliases = list(aliases)
        if normalized in normalized_aliases:
            return True
        return any(
            not re.fullmatch(rf"{re.escape(normalized)}\.\d+", alias)
            and (normalized in alias or alias in normalized)
            for alias in normalized_aliases
        )

    @staticmethod
    def _score_confidence(score: float, match_kind: str) -> float:
        if match_kind == "exact":
            return 0.98
        if score >= 80.0:
            return 0.95
        if match_kind == "query_alias":
            return 0.90
        return 0.72
