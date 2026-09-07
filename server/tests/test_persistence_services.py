"""Behavior tests for project and task persistence interfaces."""

from sheetmind.database import init_db
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
