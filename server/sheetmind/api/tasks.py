"""Analysis task routes."""

from fastapi import APIRouter, HTTPException, Query, Request

from .dependencies import projects, tasks
from .schemas import CreateTaskRequest, RenameRequest, TaskInfo
from .session import current_session_id


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
def list_tasks(
    request: Request,
    project_id: str = Query(alias="projectId"),
) -> dict[str, list[TaskInfo]]:
    if not projects.get_project(project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"tasks": [_task_info(task) for task in tasks.list_tasks(project_id)]}


@router.post("", status_code=201)
def create_task(request: Request, payload: CreateTaskRequest) -> TaskInfo:
    if not projects.get_project(payload.projectId, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return _task_info(tasks.create_task(payload.projectId))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{task_id}")
def rename_task(task_id: str, request: Request, payload: RenameRequest) -> dict[str, bool]:
    task = tasks.get_task(task_id)
    if not task or not projects.get_project(task.project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Task not found")
    if not tasks.rename_task(task_id, payload.name):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@router.delete("/{task_id}")
def delete_task(task_id: str, request: Request) -> dict[str, bool]:
    task = tasks.get_task(task_id)
    if not task or not projects.get_project(task.project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Task not found")
    if not tasks.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}
