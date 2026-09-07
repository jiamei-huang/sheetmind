"""Excel file routes."""

from fastapi import APIRouter, HTTPException

from sheetmind.database import get_db_connection

from .dependencies import excel, projects, uploads
from .schemas import ParseExcelRequest, UploadFilesRequest


router = APIRouter(prefix="/files", tags=["files"])


@router.get("/project/{project_id}")
def list_project_files(project_id: str) -> dict:
    if not projects.get_project(project_id):
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
def upload_files(request: UploadFilesRequest) -> dict:
    if not projects.get_project(request.projectId):
        raise HTTPException(status_code=404, detail="Project not found")
    parsed_files = uploads.parse_files(request.files)
    projects.add_files_to_project(request.projectId, request.files)
    return {"projectId": request.projectId, "files": parsed_files}


@router.post("/preview")
def preview_file(request: ParseExcelRequest) -> dict:
    try:
        return excel.parse_excel(request.projectId, request.fileName)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/project/{project_id}/{file_name}")
def delete_file(project_id: str, file_name: str) -> dict[str, bool]:
    try:
        deleted = excel.delete_file(project_id, file_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return {"success": True}
