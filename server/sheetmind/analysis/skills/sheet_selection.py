"""Hybrid sheet selection using metadata recall, LLM ranking, and validation."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from ..context import AnalysisContext
from ..models.configs import ModelRole
from .base import Skill, SkillError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedSheetCandidate:
    candidate_id: str
    file_name: str
    sheet_name: str
    columns: List[str]
    score: float
    reason: str
    file_id: str = ""


@dataclass
class SheetSelectionDecision:
    status: Literal["confirmed", "scope_conflict", "needs_clarification"]
    selected_files: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[RankedSheetCandidate] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    @property
    def needs_user_input(self) -> bool:
        return self.status in {"scope_conflict", "needs_clarification"}


class SheetSelectionSkill(Skill):
    """Select sheets without allowing a model to invent data sources."""

    name = "sheet_selection"
    description = "Recall sheets by metadata, rank ambiguous candidates, and surface conflicts"

    @staticmethod
    def catalog(ctx: AnalysisContext) -> List[Dict[str, Any]]:
        """Recall the source catalog once so planning and binding share one snapshot."""
        from sheetmind.services.sheet_selection import SheetSelector

        return SheetSelector().list_sheet_metadata(ctx.project_id)

    @classmethod
    def metadata_within_checked_scope(
        cls,
        ctx: AnalysisContext,
        metadata: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return metadata that planning is authorized to bind and execute."""
        if not ctx.requested_sheet_scope:
            return []
        scoped: List[Dict[str, Any]] = []
        for item in metadata:
            candidate = RankedSheetCandidate(
                candidate_id=str(item.get("candidateId", "")),
                file_id=str(item.get("fileId", "")),
                file_name=str(item.get("fileName", "")),
                sheet_name=str(item.get("sheetName", "")),
                columns=[],
                score=0.0,
                reason="",
            )
            if cls._candidate_is_checked(ctx, candidate):
                scoped.append(item)
        return scoped

    async def run(
        self,
        ctx: AnalysisContext,
        query: str,
        **kwargs: Any,
    ) -> SheetSelectionDecision:
        try:
            from sheetmind.services.sheet_selection import SheetSelector
        except ImportError as exc:
            raise SkillError(
                self.name,
                "SheetSelector could not be imported",
                detail=str(exc),
            ) from exc

        try:
            metadata = kwargs.get("metadata") or self.catalog(ctx)
        except Exception as exc:
            logger.warning("Sheet metadata recall failed: %s", exc)
            raise SkillError(
                self.name,
                f"Sheet selection failed: {exc}",
                detail=str(exc),
            ) from exc
        if not metadata:
            raise SkillError(self.name, "没有找到可用的Excel文件，请先上传数据文件。")

        target_fields = [
            str(value) for value in kwargs.get("target_fields", []) if str(value).strip()
        ]
        source_mode = str(kwargs.get("source_mode") or "single")
        ranked = self._rank_candidates(metadata, query, target_fields)
        scoped = self._scope_candidates(ctx, ranked)
        scoped_ids = {candidate.candidate_id for candidate in scoped}

        if not ctx.requested_sheet_scope:
            return SheetSelectionDecision(
                status="needs_clarification",
                candidates=ranked[:5],
                confidence=ranked[0].score,
                reason="no sheets are checked for analysis",
            )
        if not scoped:
            return SheetSelectionDecision(
                status="scope_conflict",
                candidates=ranked[:5],
                confidence=ranked[0].score,
                reason="the checked workbook or sheet is no longer available",
            )

        explicit = self._explicit_choice(query, ranked)
        if explicit:
            if explicit.candidate_id not in scoped_ids:
                return SheetSelectionDecision(
                    status="scope_conflict",
                    candidates=[explicit],
                    confidence=max(explicit.score, 0.98),
                    reason="the explicitly requested sheet is not checked",
                )
            return self._confirmed([explicit], max(explicit.score, 0.98), "user explicitly confirmed sheet")

        file_named = self._file_named_candidates(query, ranked)
        file_named_inside = [
            candidate for candidate in file_named
            if candidate.candidate_id in scoped_ids
        ]
        file_named_outside = [
            candidate for candidate in file_named
            if candidate.candidate_id not in scoped_ids
        ]
        if file_named_outside and not file_named_inside:
            return SheetSelectionDecision(
                status="scope_conflict",
                candidates=file_named_outside[:5],
                confidence=max(candidate.score for candidate in file_named_outside),
                reason="the workbook explicitly requested by the user is not checked",
            )

        preferred_ids = {
            str(value) for value in kwargs.get("preferred_candidate_ids", []) if str(value)
        }
        preferred = [item for item in ranked if item.candidate_id in preferred_ids]
        if preferred_ids and len(preferred) != len(preferred_ids):
            return SheetSelectionDecision(
                status="needs_clarification",
                candidates=preferred or ranked[:5],
                reason="the planned source is no longer available",
            )
        preferred_outside_scope = [
            candidate for candidate in preferred
            if candidate.candidate_id not in scoped_ids
        ]
        if preferred_outside_scope:
            return SheetSelectionDecision(
                status="scope_conflict",
                candidates=preferred_outside_scope[:5],
                confidence=max(candidate.score for candidate in preferred_outside_scope),
                reason="the semantic plan selected a sheet that is not checked",
            )

        explicitly_named = self._query_named_candidates(query, ranked)
        named_inside_scope = [
            candidate for candidate in explicitly_named
            if candidate.candidate_id in scoped_ids
        ]
        named_outside_scope = [
            candidate for candidate in explicitly_named
            if candidate.candidate_id not in scoped_ids
        ]
        if named_outside_scope and not named_inside_scope:
            return SheetSelectionDecision(
                status="scope_conflict",
                candidates=named_outside_scope[:5],
                confidence=max(candidate.score for candidate in named_outside_scope),
                reason="the sheet explicitly requested by the user is not checked",
            )

        explicit_multi_source = bool(
            source_mode in {"union", "join"}
            and self._requests_multiple_sheets(query)
        )
        if len(named_inside_scope) == 1:
            candidate = named_inside_scope[0]
            return self._confirmed(
                [candidate],
                max(candidate.score, 0.98),
                "query explicitly names a selected sheet",
            )

        duplicate_candidates = self._duplicate_source_candidates(scoped)
        if duplicate_candidates and not explicit_multi_source:
            return SheetSelectionDecision(
                status="needs_clarification",
                candidates=duplicate_candidates[:5],
                confidence=max(candidate.score for candidate in duplicate_candidates),
                reason="multiple checked workbook versions contain the same plausible sheet",
            )

        if preferred_ids:
            if len(preferred) == 1:
                return self._confirmed(preferred, 0.99, "source bound by the semantic plan")
            if explicit_multi_source:
                return self._confirmed(preferred, 0.98, "explicit multi-source semantic plan")
            return SheetSelectionDecision(
                status="needs_clarification",
                candidates=preferred[:5],
                confidence=0.98,
                reason=(
                    "multiple sources require independent questions or an explicit join; "
                    "they cannot be concatenated implicitly"
                ),
            )
        return await self._decide_with_scope(ctx, query, ranked, scoped, source_mode)

    async def _decide_with_scope(
        self,
        ctx: AnalysisContext,
        query: str,
        ranked: List[RankedSheetCandidate],
        scoped: List[RankedSheetCandidate],
        source_mode: str = "single",
    ) -> SheetSelectionDecision:
        explicitly_named = self._query_named_candidates(query, scoped)
        if len(explicitly_named) == 1:
            candidate = explicitly_named[0]
            return self._confirmed(
                [candidate],
                max(candidate.score, 0.98),
                "query explicitly names a selected sheet",
            )
        if len(explicitly_named) > 1:
            if source_mode in {"union", "join"} and self._requests_multiple_sheets(query):
                return self._confirmed(
                    explicitly_named,
                    0.95,
                    "query explicitly requests multiple selected sheets",
                )
            return SheetSelectionDecision(
                status="needs_clarification",
                candidates=explicitly_named[:5],
                confidence=max(candidate.score for candidate in explicitly_named),
                reason="query names multiple sheets without a clear combine or comparison request",
            )

        scoped_ids = {candidate.candidate_id for candidate in scoped}
        outside = [candidate for candidate in ranked if candidate.candidate_id not in scoped_ids]
        best_scope = max(scoped, key=lambda item: item.score)
        best_outside = max(outside, key=lambda item: item.score) if outside else None

        clear_conflict = bool(
            best_outside
            and best_outside.score >= 0.72
            and best_outside.score >= best_scope.score + 0.18
        )
        should_semantically_check = bool(
            best_outside and (clear_conflict or best_scope.score < 0.65)
        )
        llm_choice = (
            await self._llm_rank(query, [*scoped, *outside[:5]])
            if should_semantically_check
            else None
        )
        llm_finds_conflict = bool(
            llm_choice
            and llm_choice[0].candidate_id not in scoped_ids
            and llm_choice[1] >= 0.75
        )
        if (clear_conflict or llm_finds_conflict) and best_outside is not None:
            conflict_candidate = (
                llm_choice[0]
                if llm_choice and llm_choice[0].candidate_id not in scoped_ids
                and llm_choice[1] >= 0.75
                else best_outside
            )
            return SheetSelectionDecision(
                status="scope_conflict",
                candidates=[conflict_candidate],
                confidence=max(conflict_candidate.score, llm_choice[1] if llm_choice else 0.0),
                reason=(
                    f"current selection does not match query metadata; "
                    f"{conflict_candidate.sheet_name!r} is a stronger candidate"
                ),
            )

        if len(scoped) == 1:
            return self._confirmed(scoped, max(best_scope.score, 0.85), "within user-selected scope")

        if best_scope.score >= 0.78:
            second = sorted(scoped, key=lambda item: item.score, reverse=True)[1]
            if best_scope.score - second.score >= 0.16:
                return self._confirmed([best_scope], best_scope.score, "metadata match within selected scope")

        llm_choice = await self._llm_rank(query, scoped)
        if llm_choice and llm_choice[1] >= 0.80:
            return self._confirmed([llm_choice[0]], llm_choice[1], llm_choice[2])

        if source_mode in {"union", "join"} and self._requests_multiple_sheets(query):
            return self._confirmed(
                scoped,
                0.80,
                "query requests analysis across the selected sheets",
            )

        # Checkboxes define the candidate pool, not an instruction to concatenate
        # unrelated tables. Ask when neither metadata nor the model can choose.
        return SheetSelectionDecision(
            status="needs_clarification",
            candidates=sorted(scoped, key=lambda item: (-item.score, item.candidate_id))[:5],
            confidence=max(best_scope.score, llm_choice[1] if llm_choice else 0.0),
            reason="multiple selected sheets remain plausible for this question",
        )

    async def _decide_without_scope(
        self,
        ctx: AnalysisContext,
        query: str,
        ranked: List[RankedSheetCandidate],
        source_mode: str = "single",
    ) -> SheetSelectionDecision:
        if len(ranked) == 1:
            return self._confirmed(ranked, 0.95, "only available sheet")

        top = ranked[0]
        second = ranked[1]
        if top.score >= 0.82 and top.score - second.score >= 0.18:
            return self._confirmed([top], top.score, "high-confidence metadata match")

        llm_choice = await self._llm_rank(query, ranked[:8])
        if llm_choice and llm_choice[1] >= 0.82:
            return self._confirmed([llm_choice[0]], llm_choice[1], llm_choice[2])

        candidates = ranked[:5]
        return SheetSelectionDecision(
            status="needs_clarification",
            candidates=candidates,
            confidence=llm_choice[1] if llm_choice else top.score,
            reason="multiple sheets remain plausible after metadata and semantic ranking",
        )

    async def _llm_rank(
        self,
        query: str,
        candidates: List[RankedSheetCandidate],
    ) -> Optional[tuple[RankedSheetCandidate, float, str]]:
        if not candidates:
            return None
        provider = self.router.get_provider(ModelRole.SHEET_SELECTION)
        payload = [
            {
                "candidate_id": candidate.candidate_id,
                "file_name": candidate.file_name,
                "sheet_name": candidate.sheet_name,
                "columns": candidate.columns[:30],
                "metadata_score": candidate.score,
                "metadata_reason": candidate.reason,
            }
            for candidate in candidates
        ]
        system = (
            "你是Excel工作表选择器。只能从候选candidate_id中选择，不得编造文件或Sheet。"
            "根据用户问题、文件名、Sheet名和列名判断数据来源。"
            "如果信息不足必须降低confidence。只输出JSON："
            '{"candidate_id":"...","confidence":0.0,"reason":"..."}'
        )
        try:
            response = await provider.complete(
                messages=[{
                    "role": "user",
                    "content": (
                        f"用户问题：{query}\n"
                        f"候选：{json.dumps(payload, ensure_ascii=False)}"
                    ),
                }],
                system=system,
                max_tokens=512,
                temperature=0.0,
                json_mode=True,
            )
            data = self._parse_json(response)
            candidate_id = str(data.get("candidate_id", ""))
            selected = next(
                (candidate for candidate in candidates if candidate.candidate_id == candidate_id),
                None,
            )
            if selected is None:
                return None
            confidence = self._safe_confidence(data.get("confidence"))
            return selected, confidence, str(data.get("reason", "semantic sheet ranking"))
        except Exception as exc:
            logger.warning("[SheetSelection] LLM ranking failed; using metadata: %s", exc)
            return None

    @classmethod
    def _rank_candidates(
        cls,
        metadata: List[Dict[str, Any]],
        query: str,
        target_fields: List[str],
    ) -> List[RankedSheetCandidate]:
        normalized_query = cls._normalize(query)
        ranked: List[RankedSheetCandidate] = []
        for raw in metadata:
            file_id = str(raw.get("fileId", ""))
            file_name = str(raw.get("fileName", ""))
            sheet_name = str(raw.get("sheetName", ""))
            candidate_id = str(raw.get("candidateId", f"{file_id or file_name}::{sheet_name}"))
            columns = [str(column) for column in raw.get("columns", []) if str(column).strip()]
            reasons: List[str] = []
            score = 0.0

            normalized_sheet = cls._normalize(sheet_name)
            normalized_file = cls._normalize(Path(file_name).stem)
            if normalized_sheet and normalized_sheet in normalized_query:
                score = max(score, 0.96)
                reasons.append("query explicitly names the sheet")
            if normalized_file and len(normalized_file) >= 3 and normalized_file in normalized_query:
                score = max(score, 0.90)
                reasons.append("query names the workbook")

            query_column_matches = [
                column
                for column in columns
                if cls._field_match_strength(column, query) >= 0.8
            ]
            weak_query_matches = [
                column
                for column in columns
                if 0.0 < cls._field_match_strength(column, query) < 0.8
            ]
            if query_column_matches:
                score = max(score, min(0.82 + 0.04 * (len(query_column_matches) - 1), 0.94))
                reasons.append(f"query matches columns {query_column_matches[:4]}")
            elif weak_query_matches:
                score = max(score, 0.58)
                reasons.append(f"query partially matches columns {weak_query_matches[:4]}")

            target_matches = []
            for target in target_fields:
                normalized_target = cls._normalize(target)
                if not normalized_target:
                    continue
                if any(cls._field_match_strength(column, target) >= 0.8 for column in columns):
                    target_matches.append(target)
            if target_matches:
                score = max(score, min(0.82 + 0.04 * (len(target_matches) - 1), 0.94))
                reasons.append(f"planned fields match {target_matches[:4]}")

            samples = [str(value) for value in raw.get("sampleValues", [])]
            sample_matches = [value for value in samples if len(value) >= 2 and value in query]
            if sample_matches:
                score = max(score, 0.62)
                reasons.append(f"query matches sample values {sample_matches[:3]}")

            recency_rank = int(raw.get("recencyRank", 0) or 0)
            score = min(score + max(0.0, 0.04 - recency_rank * 0.01), 1.0)
            ranked.append(RankedSheetCandidate(
                candidate_id=candidate_id,
                file_id=file_id,
                file_name=file_name,
                sheet_name=sheet_name,
                columns=columns,
                score=round(score, 4),
                reason="; ".join(reasons) or "no direct metadata match",
            ))
        return sorted(ranked, key=lambda item: (-item.score, item.candidate_id))

    @classmethod
    def _scope_candidates(
        cls,
        ctx: AnalysisContext,
        candidates: List[RankedSheetCandidate],
    ) -> List[RankedSheetCandidate]:
        return [
            candidate for candidate in candidates
            if cls._candidate_is_checked(ctx, candidate)
        ]

    @staticmethod
    def _candidate_is_checked(
        ctx: AnalysisContext,
        candidate: RankedSheetCandidate,
    ) -> bool:
        for scope in ctx.requested_sheet_scope:
            file_id = str(scope.get("fileId", ""))
            file_name = str(scope.get("fileName", ""))
            sheets = {str(sheet) for sheet in scope.get("sheets", [])}
            if file_id and file_id != candidate.file_id:
                continue
            if not file_id and file_name and file_name != candidate.file_name:
                continue
            if candidate.sheet_name not in sheets:
                continue
            return True
        return False

    @classmethod
    def _explicit_choice(
        cls,
        query: str,
        candidates: List[RankedSheetCandidate],
    ) -> Optional[RankedSheetCandidate]:
        sheet_match = re.search(r"使用(?:文件[\"“](.*?)[\"”]的)?工作表[\"“](.*?)[\"”]继续", query)
        if sheet_match:
            requested_file = sheet_match.group(1)
            requested_sheet = sheet_match.group(2)
            exact = [
                candidate
                for candidate in candidates
                if candidate.sheet_name == requested_sheet
                and (not requested_file or candidate.file_name == requested_file)
            ]
            if len(exact) == 1:
                return exact[0]
        return None

    @classmethod
    def _named_candidates(
        cls,
        query: str,
        candidates: List[RankedSheetCandidate],
    ) -> List[RankedSheetCandidate]:
        normalized_query = cls._normalize(query)
        return [
            candidate
            for candidate in candidates
            if len(cls._normalize(candidate.sheet_name)) >= 2
            and cls._normalize(candidate.sheet_name) in normalized_query
        ]

    @classmethod
    def _file_named_candidates(
        cls,
        query: str,
        candidates: List[RankedSheetCandidate],
    ) -> List[RankedSheetCandidate]:
        normalized_query = cls._normalize(query)
        lower_query = query.lower()
        matches: List[tuple[int, RankedSheetCandidate]] = []
        for candidate in candidates:
            file_name = candidate.file_name.lower()
            stem = cls._normalize(Path(candidate.file_name).stem)
            full_name_match = bool(file_name and file_name in lower_query)
            contextual_stem_match = bool(
                len(stem) >= 3
                and stem in normalized_query
                and any(marker in lower_query for marker in ("文件", "excel", "工作簿", ".xlsx", ".xls"))
            )
            if full_name_match or contextual_stem_match:
                matches.append((len(stem), candidate))
        if not matches:
            return []
        longest = max(length for length, _candidate in matches)
        return [candidate for length, candidate in matches if length == longest]

    @classmethod
    def _query_named_candidates(
        cls,
        query: str,
        candidates: List[RankedSheetCandidate],
    ) -> List[RankedSheetCandidate]:
        sheet_matches = cls._named_candidates(query, candidates)
        file_matches = cls._file_named_candidates(query, candidates)
        if sheet_matches and file_matches:
            file_ids = {candidate.candidate_id for candidate in file_matches}
            intersection = [
                candidate for candidate in sheet_matches
                if candidate.candidate_id in file_ids
            ]
            if intersection:
                return intersection
        return file_matches or sheet_matches

    @classmethod
    def _duplicate_source_candidates(
        cls,
        candidates: List[RankedSheetCandidate],
    ) -> List[RankedSheetCandidate]:
        if len(candidates) < 2:
            return []
        best_score = max(candidate.score for candidate in candidates)
        plausible = [
            candidate for candidate in candidates
            if candidate.score >= max(0.58, best_score - 0.08)
        ]
        duplicates: List[RankedSheetCandidate] = []
        for index, left in enumerate(plausible):
            left_columns = {cls._normalize(column) for column in left.columns if cls._normalize(column)}
            for right in plausible[index + 1:]:
                same_file = bool(
                    left.file_id and right.file_id and left.file_id == right.file_id
                ) or (
                    not left.file_id and not right.file_id and left.file_name == right.file_name
                )
                if same_file:
                    continue
                right_columns = {cls._normalize(column) for column in right.columns if cls._normalize(column)}
                union = left_columns | right_columns
                schema_similarity = len(left_columns & right_columns) / len(union) if union else 0.0
                same_sheet = cls._normalize(left.sheet_name) == cls._normalize(right.sheet_name)
                if same_sheet or schema_similarity >= 0.8:
                    duplicates.extend((left, right))
        return cls._unique_candidates(
            sorted(duplicates, key=lambda item: (-item.score, item.candidate_id))
        )

    @classmethod
    def _requests_multiple_sheets(cls, query: str) -> bool:
        normalized = cls._normalize(query)
        return any(
            signal in normalized
            for signal in (
                "合并", "拼接", "汇总所有", "全部sheet", "所有sheet",
                "跨sheet", "多个sheet", "两张表", "两个表", "对比", "比较",
                "左联", "右联", "左连接", "右连接", "内连接", "外连接",
                "combine", "merge", "join", "allsheets", "acrosssheets",
            )
        )

    @staticmethod
    def _confirmed(
        candidates: List[RankedSheetCandidate],
        confidence: float,
        reason: str,
    ) -> SheetSelectionDecision:
        grouped: Dict[tuple[str, str], List[str]] = {}
        for candidate in candidates:
            grouped.setdefault(
                (candidate.file_id, candidate.file_name), []
            ).append(candidate.sheet_name)
        selected_files = []
        for (file_id, file_name), sheets in grouped.items():
            item = {
                "fileName": file_name,
                "sheets": sheets,
                "confidence": confidence,
                "reason": reason,
            }
            if file_id:
                item["fileId"] = file_id
            selected_files.append(item)
        return SheetSelectionDecision(
            status="confirmed",
            selected_files=selected_files,
            candidates=candidates,
            confidence=confidence,
            reason=reason,
        )

    @staticmethod
    def _unique_candidates(
        candidates: List[RankedSheetCandidate],
    ) -> List[RankedSheetCandidate]:
        seen = set()
        result = []
        for candidate in candidates:
            if candidate.candidate_id in seen:
                continue
            seen.add(candidate.candidate_id)
            result.append(candidate)
        return result

    @staticmethod
    def _normalize(value: str) -> str:
        text = str(value).lower()
        for source, target in {
            "人民币": "rmb", "cny": "rmb", "美元": "usd", "美金": "usd",
        }.items():
            text = text.replace(source, target)
        return re.sub(r"[\s_\-（）()【】\[\]{}.,，。:：]", "", text)

    @classmethod
    def _field_match_strength(cls, column: str, text: str) -> float:
        column_normalized = cls._normalize(column)
        text_normalized = cls._normalize(text)
        if not column_normalized or len(column_normalized) < 2:
            return 0.0
        column_qualifiers = {
            qualifier for qualifier in ("rmb", "usd") if qualifier in column_normalized
        }
        text_qualifiers = {
            qualifier for qualifier in ("rmb", "usd") if qualifier in text_normalized
        }
        base_column = column_normalized
        for qualifier in ("rmb", "usd"):
            base_column = base_column.replace(qualifier, "")
        if not base_column or base_column not in text_normalized:
            return 0.0
        if text_qualifiers:
            if column_qualifiers.intersection(text_qualifiers):
                return 1.0
            if column_qualifiers:
                return 0.0
            return 0.5
        return 1.0 if column_normalized in text_normalized else 0.8

    @staticmethod
    def _safe_confidence(value: Any) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _parse_json(response: str) -> Dict[str, Any]:
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
