"""Project routes."""

from fastapi import APIRouter, HTTPException, Request

from .dependencies import projects, tasks, uploads
from .schemas import CreateProjectRequest, ProjectInfo, RenameRequest
from .session import current_session_id


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects(request: Request) -> dict[str, list[ProjectInfo]]:
    session_id = current_session_id(request)
    return {
        "projects": [
            ProjectInfo(
                projectId=item.project_id,
                projectName=item.project_name,
                createdAt=item.created_at.isoformat(),
            )
            for item in projects.list_projects(session_id)
        ]
    }


@router.post("", status_code=201)
def create_project(request: Request, payload: CreateProjectRequest) -> dict:
    try:
        parsed_files = uploads.parse_files(payload.files)
        result = projects.get_or_create_project(
            payload.projectName,
            payload.files,
            current_session_id(request),
        )
        if result.created:
            tasks.create_task(result.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for info, write in zip(parsed_files, result.files):
        info.update(
            fileId=write.file_id,
            fileName=write.file_name,
            uploadStatus=write.status,
        )
    return {
        "projectId": result.project_id,
        "projectName": payload.projectName.strip(),
        "isExistingProject": not result.created,
        "files": parsed_files,
    }


@router.patch("/{project_id}")
def rename_project(
    project_id: str,
    payload: RenameRequest,
    request: Request,
) -> dict[str, bool]:
    try:
        updated = projects.rename_project(
            project_id, payload.name, current_session_id(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}


@router.delete("/{project_id}")
def delete_project(project_id: str, request: Request) -> dict[str, bool]:
    if not projects.delete_project(project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}
