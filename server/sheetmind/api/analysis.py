"""Native ResultBlocks analysis routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from sheetmind.analysis.streaming.emitter import StreamEmitter

from .dependencies import analysis_agent, analysis_contexts, conversations, tasks
from .schemas import AnalyzeRequest


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysis", tags=["analysis"])


def _analysis_context(request: AnalyzeRequest):
    task = tasks.get_task(request.taskId)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return analysis_contexts.get_or_create(task.task_id, task.project_id)


def _summary(result) -> str:
    return "\n\n".join(result.all_summaries())


async def _run_stream(ctx, request: AnalyzeRequest, emitter: StreamEmitter) -> None:
    try:
        result = await analysis_agent.run(ctx, request.query.strip(), emitter=emitter)
        conversations.add_message(request.taskId, "assistant", _summary(result))
    except Exception as exc:
        logger.exception("Analysis failed for task %s", request.taskId)
        if not emitter.is_closed:
            await emitter.emit_error(str(exc))


@router.post("/stream")
async def analyze_stream(request: AnalyzeRequest):
    ctx = _analysis_context(request)
    conversations.add_message(request.taskId, "user", request.query.strip())
    emitter = StreamEmitter()
    asyncio.create_task(_run_stream(ctx, request, emitter))
    return StreamingResponse(
        emitter.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("")
async def analyze(request: AnalyzeRequest) -> dict:
    ctx = _analysis_context(request)
    conversations.add_message(request.taskId, "user", request.query.strip())
    result = await analysis_agent.run(ctx, request.query.strip())
    conversations.add_message(request.taskId, "assistant", _summary(result))
    return result.model_dump()
