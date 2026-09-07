"""Project routes."""

from fastapi import APIRouter, HTTPException

from .dependencies import projects, tasks, uploads
from .schemas import CreateProjectRequest, ProjectInfo, RenameRequest


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects() -> dict[str, list[ProjectInfo]]:
    return {
        "projects": [
            ProjectInfo(
                projectId=item.project_id,
                projectName=item.project_name,
                createdAt=item.created_at.isoformat(),
            )
            for item in projects.list_projects()
        ]
    }


@router.post("", status_code=201)
def create_project(request: CreateProjectRequest) -> dict:
    parsed_files = uploads.parse_files(request.files)
    try:
        project_id = projects.create_project(request.projectName, request.files)
        tasks.create_task(project_id)
    except ValueError as exc:
        status = 409 if "already exists" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"projectId": project_id, "files": parsed_files}


@router.patch("/{project_id}")
def rename_project(project_id: str, request: RenameRequest) -> dict[str, bool]:
    try:
        updated = projects.rename_project(project_id, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict[str, bool]:
    if not projects.delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}
