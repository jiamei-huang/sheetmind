"""Analysis task routes."""

from fastapi import APIRouter, HTTPException, Query

from .dependencies import tasks
from .schemas import CreateTaskRequest, RenameRequest, TaskInfo


router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_info(task) -> TaskInfo:
    return TaskInfo(
        taskId=task.task_id,
        taskNumber=task.task_number,
        projectId=task.project_id,
        createdAt=task.created_at.isoformat(),
        status=task.status,
        title=task.title,
    )


@router.get("")
def list_tasks(project_id: str = Query(alias="projectId")) -> dict[str, list[TaskInfo]]:
    return {"tasks": [_task_info(task) for task in tasks.list_tasks(project_id)]}


@router.post("", status_code=201)
def create_task(request: CreateTaskRequest) -> TaskInfo:
    try:
        return _task_info(tasks.create_task(request.projectId))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{task_id}")
def rename_task(task_id: str, request: RenameRequest) -> dict[str, bool]:
    if not tasks.rename_task(task_id, request.name):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@router.delete("/{task_id}")
def delete_task(task_id: str) -> dict[str, bool]:
    if not tasks.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}
