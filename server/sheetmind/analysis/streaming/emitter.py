"""
SheetMind Runtime — SSE Streaming Emitter
=============================================
Implements the five SSE event types defined in the spec (§7):

  thinking   — initial status, shown immediately ("正在分析您的问题...")
  progress   — intermediate step updates ("正在执行数据查询...")
  repairing  — repair loop triggered ("修复执行错误，重试中...")
  done       — final result (contains the full ResultBlocks payload)
  error      — analysis failed ("分析未能完成，请重试。")

Wire format: standard Server-Sent Events
  data: {"event": "thinking", "message": "..."}\n\n

Usage inside SheetMindAgent:
    emitter = StreamEmitter()
    # pass emitter into skills so they can emit progress
    await emitter.emit_thinking()
    await emitter.emit_progress("正在执行数据查询...")
    await emitter.emit_done(result_dict)

Usage in FastAPI endpoint:
    from fastapi.responses import StreamingResponse

    @router.post("/api/analysis/stream")
    async def analyze_stream(body: AnalyzeRequest):
        ctx = ...
        emitter = StreamEmitter()
        asyncio.create_task(agent.run(ctx, body.query, emitter=emitter))
        return StreamingResponse(emitter.stream(), media_type="text/event-stream")
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, Optional


# ---------------------------------------------------------------------------
# Low-level SSE frame builders
# ---------------------------------------------------------------------------

def _sse_frame(event: str, **payload: Any) -> str:
    """Encode one SSE data frame.  Standard format: 'data: <json>\\n\\n'."""
    data = {"event": event, **payload}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def thinking_frame(message: str = "正在分析您的问题...") -> str:
    return _sse_frame("thinking", message=message)


def progress_frame(message: str) -> str:
    return _sse_frame("progress", message=message)


def repairing_frame(message: str = "修复执行错误，重试中...") -> str:
    return _sse_frame("repairing", message=message)


def done_frame(result: Dict[str, Any]) -> str:
    return _sse_frame("done", result=result)


def error_frame(message: str = "分析未能完成，请重试。") -> str:
    return _sse_frame("error", message=message)


# ---------------------------------------------------------------------------
# StreamEmitter — async queue used by SheetMindAgent
# ---------------------------------------------------------------------------

class StreamEmitter:
    """
    Async queue-based SSE emitter.

    SheetMindAgent creates one emitter per request and passes it to skills.
    Skills call `emitter.emit_*()` to push frames.
    The FastAPI endpoint streams frames to the browser via `emitter.stream()`.

    The emitter is closed (sentinel None pushed) automatically by
    emit_done() and emit_error(), after which stream() exits cleanly.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

    # ------------------------------------------------------------------
    # Emit helpers — called by SheetMindAgent / skills
    # ------------------------------------------------------------------

    async def emit(self, frame: str) -> None:
        if not self._closed:
            await self._queue.put(frame)

    async def emit_thinking(self, message: str = "正在分析您的问题...") -> None:
        await self.emit(thinking_frame(message))

    async def emit_progress(self, message: str) -> None:
        await self.emit(progress_frame(message))

    async def emit_repairing(self, message: str = "修复执行错误，重试中...") -> None:
        await self.emit(repairing_frame(message))

    async def emit_done(self, result: Dict[str, Any]) -> None:
        await self.emit(done_frame(result))
        await self._close()

    async def emit_error(self, message: str = "分析未能完成，请重试。") -> None:
        await self.emit(error_frame(message))
        await self._close()

    async def _close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._queue.put(None)  # sentinel — signals stream() to exit

    # ------------------------------------------------------------------
    # stream() — consumed by the FastAPI endpoint
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncGenerator[str, None]:
        """
        Async generator that yields SSE frames until done or error.
        Exits when emit_done() or emit_error() sends the sentinel.
        """
        while True:
            frame = await self._queue.get()
            if frame is None:
                break
            yield frame

    # ------------------------------------------------------------------
    # Non-streaming mode
    # ------------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed
