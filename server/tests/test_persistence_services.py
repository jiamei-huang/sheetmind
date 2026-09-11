"""Behavior tests for project and task persistence interfaces."""

import base64
import sqlite3
from types import SimpleNamespace

from sheetmind.database import init_db
from sheetmind.services.anonymous_sessions import AnonymousSessionService
from sheetmind.services.projects import ProjectService
from sheetmind.services.tasks import TaskService


def isolated_database(tmp_path, monkeypatch):
    path = tmp_path / "sheetmind-test.db"
    monkeypatch.setenv("SHEETMIND_DB_PATH", str(path))
    init_db()
    return path


def test_project_and_task_lifecycle(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    projects = ProjectService()
    tasks = TaskService()

    project_id = projects.create_project("Sales", [])
    first = tasks.create_task(project_id)
    second = tasks.create_task(project_id)

    assert [task.task_number for task in tasks.list_tasks(project_id)] == [1, 2]
    assert tasks.rename_task(first.task_id, "Regional analysis")
    assert tasks.get_task(first.task_id).title == "Regional analysis"
    assert projects.rename_project(project_id, "Sales 2026")
    assert projects.get_project(project_id).project_name == "Sales 2026"
    assert tasks.delete_task(second.task_id)
    assert projects.delete_project(project_id)
    assert tasks.get_task(first.task_id) is None


def test_project_names_are_unique(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    projects = ProjectService()
    projects.create_project("Sales", [])

    try:
        projects.create_project("Sales", [])
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate project names must be rejected")


def test_anonymous_sessions_isolate_projects_and_allow_same_names(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    sessions = AnonymousSessionService()
    projects = ProjectService()
    first = sessions.resolve(None)
    second = sessions.resolve(None)

    first_project = projects.create_project("Default Project", [], first.session_id)
    second_project = projects.create_project("Default Project", [], second.session_id)

    assert [item.project_id for item in projects.list_projects(first.session_id)] == [
        first_project
    ]
    assert [item.project_id for item in projects.list_projects(second.session_id)] == [
        second_project
    ]
    assert projects.get_project(first_project, second.session_id) is None


def test_first_session_claims_legacy_projects(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    projects = ProjectService()
    legacy_project = projects.create_project("Legacy", [])
    session = AnonymousSessionService().resolve(None)

    claimed = projects.list_projects(session.session_id)

    assert [item.project_id for item in claimed] == [legacy_project]
    assert projects.get_project(legacy_project, session.session_id) is not None


def test_legacy_claim_preserves_projects_with_duplicate_names(tmp_path, monkeypatch):
    path = isolated_database(tmp_path, monkeypatch)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO projects (project_id, session_id, project_name, created_at)
            VALUES ('older', NULL, '我的项目', '2026-01-01T00:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, session_id, project_name, created_at)
            VALUES ('newer', NULL, '我的项目', '2026-02-01T00:00:00')
            """
        )
    session = AnonymousSessionService().resolve(None)

    claimed = ProjectService().list_projects(session.session_id)

    assert [(item.project_id, item.project_name) for item in claimed] == [
        ("newer", "我的项目"),
        ("older", "我的项目 (1)"),
    ]


def test_file_upload_is_idempotent_and_replaces_changed_content(tmp_path, monkeypatch):
    path = isolated_database(tmp_path, monkeypatch)
    session = AnonymousSessionService().resolve(None)
    projects = ProjectService()
    project_id = projects.create_project("Storage", [], session.session_id)

    def upload(content: bytes):
        return SimpleNamespace(
            fileName="storage.xlsx",
            base64=base64.b64encode(content).decode("ascii"),
        )

    [created] = projects.add_files_to_project(
        project_id, [upload(b"version-one")], session.session_id
    )
    [reused] = projects.add_files_to_project(
        project_id, [upload(b"version-one")], session.session_id
    )
    [replaced] = projects.add_files_to_project(
        project_id, [upload(b"version-two")], session.session_id
    )

    assert created.status == "created"
    assert reused.status == "reused"
    assert replaced.status == "replaced"
    assert created.file_id == reused.file_id == replaced.file_id
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT file_data FROM files WHERE project_id = ? AND file_name = ?",
            (project_id, "storage.xlsx"),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(b"version-two",)]
