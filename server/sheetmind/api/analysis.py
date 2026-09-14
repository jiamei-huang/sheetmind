"""Native ResultBlocks analysis routes."""

from __future__ import annotations

import asyncio
import io
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from sheetmind.analysis.streaming.emitter import StreamEmitter

from .dependencies import (
    analysis_agent,
    analysis_artifacts,
    analysis_contexts,
    conversations,
    projects,
    tasks,
)
from .schemas import AnalyzeRequest
from .session import current_session_id


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysis", tags=["analysis"])


def _analysis_context(payload: AnalyzeRequest, session_id: str):
    task = tasks.get_task(payload.taskId)
    if not task or not projects.get_project(task.project_id, session_id):
        raise HTTPException(status_code=404, detail="Task not found")
    ctx = analysis_contexts.get_or_create(task.task_id, task.project_id)
    return ctx


def _apply_requested_scope(ctx, payload: AnalyzeRequest) -> None:
    ctx.set_requested_sheet_scope([
        selection.model_dump() for selection in payload.selectedFiles
    ])


def _summary(result) -> str:
    return "\n\n".join(result.all_summaries())


def _result_metadata(result, run_id: str) -> dict:
    return {
        "runId": run_id,
        "status": result.status,
        "result": result.model_dump(mode="json"),
    }


async def _run_stream(
    ctx,
    request: AnalyzeRequest,
    emitter: StreamEmitter,
    run_id: str,
) -> None:
    try:
        async with ctx._run_lock:
            _apply_requested_scope(ctx, request)
            result = await analysis_agent.run(
                ctx,
                request.query.strip(),
                emitter=emitter,
                run_id=run_id,
            )
            analysis_contexts.set(request.taskId, ctx)
            conversations.add_message(
                request.taskId,
                "assistant",
                _summary(result),
                metadata=_result_metadata(result, run_id),
            )
    except Exception as exc:
        logger.exception("Analysis failed for task %s", request.taskId)
        conversations.add_message(
            request.taskId,
            "assistant",
            "Analysis was interrupted. Submit the question again.",
            metadata={"runId": run_id, "status": "failed"},
        )
        if not emitter.is_closed:
            await emitter.emit_error(str(exc))


@router.post("/stream")
async def analyze_stream(request: Request, payload: AnalyzeRequest):
    ctx = _analysis_context(payload, current_session_id(request))
    run_id = uuid.uuid4().hex
    conversations.add_message(
        payload.taskId,
        "user",
        payload.query.strip(),
        metadata={"runId": run_id, "status": "running"},
    )
    emitter = StreamEmitter()
    asyncio.create_task(_run_stream(ctx, payload, emitter, run_id))
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
    run_id = uuid.uuid4().hex
    conversations.add_message(
        payload.taskId,
        "user",
        payload.query.strip(),
        metadata={"runId": run_id, "status": "running"},
    )
    try:
        async with ctx._run_lock:
            _apply_requested_scope(ctx, payload)
            result = await analysis_agent.run(ctx, payload.query.strip(), run_id=run_id)
            analysis_contexts.set(payload.taskId, ctx)
            conversations.add_message(
                payload.taskId,
                "assistant",
                _summary(result),
                metadata=_result_metadata(result, run_id),
            )
        return result.model_dump()
    except Exception:
        logger.exception("Analysis failed for task %s", payload.taskId)
        conversations.add_message(
            payload.taskId,
            "assistant",
            "Analysis was interrupted. Submit the question again.",
            metadata={"runId": run_id, "status": "failed"},
        )
        raise


@router.get("/artifacts/{artifact_id}/excel")
def export_artifact(artifact_id: str, request: Request):
    """Download the complete computed result, never the bounded UI preview."""
    task_id = request.query_params.get("taskId", "")
    task = tasks.get_task(task_id) if task_id else None
    if not task or not projects.get_project(task.project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Analysis artifact not found")
    payload = analysis_artifacts.to_excel(artifact_id, task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Analysis artifact not found")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="analysis-{artifact_id[:8]}.xlsx"'
        },
    )
