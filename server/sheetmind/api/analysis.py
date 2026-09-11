"""Native ResultBlocks analysis routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from sheetmind.analysis.streaming.emitter import StreamEmitter

from .dependencies import analysis_agent, analysis_contexts, conversations, projects, tasks
from .schemas import AnalyzeRequest
from .session import current_session_id


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysis", tags=["analysis"])


def _analysis_context(payload: AnalyzeRequest, session_id: str):
    task = tasks.get_task(payload.taskId)
    if not task or not projects.get_project(task.project_id, session_id):
        raise HTTPException(status_code=404, detail="Task not found")
    ctx = analysis_contexts.get_or_create(task.task_id, task.project_id)
    if payload.selectedFiles:
        ctx.requested_sheet_scope = [
            selection.model_dump() for selection in payload.selectedFiles
        ]
        ctx.selected_sheets = [
            sheet
            for selection in payload.selectedFiles
            for sheet in selection.sheets
        ]
    return ctx


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
async def analyze_stream(request: Request, payload: AnalyzeRequest):
    ctx = _analysis_context(payload, current_session_id(request))
    conversations.add_message(payload.taskId, "user", payload.query.strip())
    emitter = StreamEmitter()
    asyncio.create_task(_run_stream(ctx, payload, emitter))
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
async def analyze(request: Request, payload: AnalyzeRequest) -> dict:
    ctx = _analysis_context(payload, current_session_id(request))
    conversations.add_message(payload.taskId, "user", payload.query.strip())
    result = await analysis_agent.run(ctx, payload.query.strip())
    conversations.add_message(payload.taskId, "assistant", _summary(result))
    return result.model_dump()
