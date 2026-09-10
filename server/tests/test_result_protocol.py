"""Tests for the public ResultBlocks and SSE interfaces."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from sheetmind.analysis import (
    ChartBlock,
    ChartSeries,
    FieldCandidate,
    FieldResolutionBlock,
    MetricBlock,
    ResultBlocks,
    SummaryBlock,
    TableBlock,
)
from sheetmind.analysis.streaming.emitter import StreamEmitter
from sheetmind.analysis.validators.result_validator import (
    ResultValidationError,
    validate_result,
)


def test_result_blocks_serializes_native_protocol():
    result = ResultBlocks(
        blocks=[
            MetricBlock(label="Revenue", value=1200, unit="CNY"),
            TableBlock(columns=["region", "sales"], rows=[{"region": "East", "sales": 1200}]),
            ChartBlock(
                chart_type="bar",
                labels=["East"],
                series=[ChartSeries(name="Sales", values=[1200])],
            ),
            SummaryBlock(content="East leads sales."),
        ]
    )

    payload = result.model_dump()
    assert payload["type"] == "result_blocks"
    assert [block["kind"] for block in payload["blocks"]] == [
        "metric",
        "table",
        "chart",
        "summary",
    ]


def test_result_blocks_rejects_unknown_block_kind():
    with pytest.raises(ValidationError):
        ResultBlocks(blocks=[{"kind": "image", "data": "..."}])


def test_result_blocks_serializes_field_clarification():
    result = ResultBlocks(blocks=[
        FieldResolutionBlock(
            status="needs_clarification",
            reference="金额",
            message="“金额”可能对应多个字段，请选择。",
            candidates=[
                FieldCandidate(column="金额", confidence=0.5, reason="名称匹配"),
                FieldCandidate(column="金额（RMB）", confidence=0.5, reason="名称匹配"),
            ],
        )
    ])

    payload = result.model_dump()
    assert payload["blocks"][0]["kind"] == "field_resolution"
    assert payload["blocks"][0]["status"] == "needs_clarification"
    assert [item["column"] for item in payload["blocks"][0]["candidates"]] == [
        "金额",
        "金额（RMB）",
    ]


def test_field_clarification_requires_multiple_candidates():
    result = ResultBlocks(blocks=[
        FieldResolutionBlock(
            status="needs_clarification",
            reference="金额",
            message="请选择字段。",
            candidates=[FieldCandidate(column="金额", confidence=0.5, reason="名称匹配")],
        )
    ])

    with pytest.raises(ResultValidationError):
        validate_result(result)


def test_result_helpers_find_typed_blocks():
    table = TableBlock(columns=["value"], rows=[{"value": 1}])
    chart = ChartBlock(
        chart_type="line",
        labels=["Jan"],
        series=[ChartSeries(name="Sales", values=[1])],
    )
    result = ResultBlocks(blocks=[table, chart, SummaryBlock(content="Summary")])

    assert result.has_table
    assert result.has_chart
    assert result.has_summary
    assert result.first_table() == table
    assert result.first_chart() == chart
    assert result.all_summaries() == ["Summary"]


def test_stream_emitter_sends_native_result_and_closes():
    async def scenario():
        emitter = StreamEmitter()
        result = ResultBlocks(blocks=[SummaryBlock(content="Done")]).model_dump()
        await emitter.emit_thinking("Starting")
        await emitter.emit_done(result)
        frames = [frame async for frame in emitter.stream()]
        return emitter, result, frames

    emitter, result, frames = asyncio.run(scenario())
    payloads = [json.loads(frame.removeprefix("data: ")) for frame in frames]
    assert payloads[0] == {"event": "thinking", "message": "Starting"}
    assert payloads[1]["event"] == "done"
    assert payloads[1]["result"] == result
    assert emitter.is_closed


def test_stream_emitter_error_closes_stream():
    async def scenario():
        emitter = StreamEmitter()
        await emitter.emit_error("failed")
        frames = [frame async for frame in emitter.stream()]
        return frames

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert json.loads(frames[0].removeprefix("data: ")) == {
        "event": "error",
        "message": "failed",
    }
