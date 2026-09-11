"""Conversation history routes."""

from fastapi import APIRouter, HTTPException, Request

from .dependencies import analysis_contexts, conversations, projects, tasks
from .session import current_session_id


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/{task_id}")
def history(task_id: str, request: Request) -> dict:
    task = tasks.get_task(task_id)
    if not task or not projects.get_project(task.project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"taskId": task_id, "messages": conversations.get_conversation_history(task_id)}


@router.delete("/{task_id}")
def clear(task_id: str, request: Request) -> dict[str, bool]:
    task = tasks.get_task(task_id)
    if not task or not projects.get_project(task.project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Task not found")
    conversations.clear_conversation(task_id)
    analysis_contexts.delete(task_id)
    return {"success": True}
