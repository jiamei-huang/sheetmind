"""Excel file routes."""

from fastapi import APIRouter, HTTPException, Request

from sheetmind.database import get_db_connection

from .dependencies import excel, projects, uploads
from .schemas import ParseExcelRequest, UploadFilesRequest
from .session import current_session_id


router = APIRouter(prefix="/files", tags=["files"])


@router.get("/project/{project_id}")
def list_project_files(project_id: str, request: Request) -> dict:
    if not projects.get_project(project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Project not found")

    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT file_id, file_name, length(file_data), created_at
            FROM files
            WHERE project_id = ?
            ORDER BY created_at ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        try:
            parsed = excel.parse_excel(project_id, row[1])
            sheet_names = [sheet["sheetName"] for sheet in parsed["sheets"]]
        except Exception:
            sheet_names = []
        result.append(
            {
                "fileId": row[0],
                "fileName": row[1],
                "fileSize": row[2] or 0,
                "sheets": sheet_names,
                "createdAt": str(row[3]),
            }
        )
    return {"projectId": project_id, "files": result}


@router.post("", status_code=201)
def upload_files(request: Request, payload: UploadFilesRequest) -> dict:
    session_id = current_session_id(request)
    if not projects.get_project(payload.projectId, session_id):
        raise HTTPException(status_code=404, detail="Project not found")
    parsed_files = uploads.parse_files(payload.files)
    write_results = projects.add_files_to_project(
        payload.projectId, payload.files, session_id
    )
    for info, write in zip(parsed_files, write_results):
        info.update(fileId=write.file_id, uploadStatus=write.status)
    return {"projectId": payload.projectId, "files": parsed_files}


@router.post("/preview")
def preview_file(request: Request, payload: ParseExcelRequest) -> dict:
    if not projects.get_project(payload.projectId, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return excel.parse_excel(payload.projectId, payload.fileName)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/project/{project_id}/{file_name}")
def delete_file(project_id: str, file_name: str, request: Request) -> dict[str, bool]:
    if not projects.get_project(project_id, current_session_id(request)):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        deleted = excel.delete_file(project_id, file_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return {"success": True}
