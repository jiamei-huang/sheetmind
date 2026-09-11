"""HTTP request and response models."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FileUpload(BaseModel):
    fileName: str = Field(min_length=1)
    base64: str = Field(min_length=1)


class CreateProjectRequest(BaseModel):
    projectName: str = Field(min_length=1, max_length=120)
    files: list[FileUpload] = Field(default_factory=list)


class UploadFilesRequest(BaseModel):
    projectId: str = Field(min_length=1)
    files: list[FileUpload] = Field(min_length=1)


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CreateTaskRequest(BaseModel):
    projectId: str = Field(min_length=1)


class ParseExcelRequest(BaseModel):
    projectId: str = Field(min_length=1)
    fileName: str = Field(min_length=1)


class AnalyzeSelectedFile(BaseModel):
    fileName: str = Field(min_length=1)
    sheets: list[str] = Field(min_length=1)


class AnalyzeRequest(BaseModel):
    taskId: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    selectedFiles: list[AnalyzeSelectedFile] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    projectId: str
    projectName: str
    createdAt: str


class TaskInfo(BaseModel):
    taskId: str
    taskNumber: int
    projectId: str
    createdAt: str
    status: str
    title: Optional[str] = None


class SheetPreview(BaseModel):
    sheetName: str
    columns: list[str]
    preview: list[dict[str, Any]]
    rowCount: int
    error: Optional[str] = None
