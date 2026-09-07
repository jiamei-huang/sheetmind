"""Conversation history routes."""

from fastapi import APIRouter, HTTPException

from .dependencies import analysis_contexts, conversations, tasks


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/{task_id}")
def history(task_id: str) -> dict:
    if not tasks.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"taskId": task_id, "messages": conversations.get_conversation_history(task_id)}


@router.delete("/{task_id}")
def clear(task_id: str) -> dict[str, bool]:
    if not tasks.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    conversations.clear_conversation(task_id)
    analysis_contexts.delete(task_id)
    return {"success": True}
