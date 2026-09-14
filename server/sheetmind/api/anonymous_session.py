"""Anonymous Demo session routes."""

from fastapi import APIRouter, Request

from .dependencies import analysis_contexts, anonymous_sessions
from .session import current_session_id


router = APIRouter(prefix="/session", tags=["session"])


@router.get("")
def get_session(request: Request) -> dict:
    return {
        "anonymous": True,
        "expiresAt": request.state.anonymous_session_expires_at.isoformat(),
        "retentionDays": anonymous_sessions.lifetime_days,
    }


@router.delete("")
def reset_session(request: Request) -> dict[str, bool]:
    task_ids = anonymous_sessions.delete(current_session_id(request))
    for task_id in task_ids:
        analysis_contexts.delete(task_id)
    request.state.clear_anonymous_session_cookie = True
    return {"success": True}
