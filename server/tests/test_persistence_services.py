"""Behavior tests for project and task persistence interfaces."""

import base64
import io
import sqlite3
from types import SimpleNamespace

import pandas as pd

from sheetmind.analysis.artifacts import AnalysisArtifactStore
from sheetmind.analysis.context import (
    AnalysisContext,
    QuestionResult,
    ResultBlocks,
    ResultLineage,
    SummaryBlock,
    TableBlock,
)
from sheetmind.analysis.context_store import ContextStore
from sheetmind.analysis.tools.dataframe_loader import DataframeLoaderTool
from sheetmind.database import init_db
from sheetmind.services.anonymous_sessions import AnonymousSessionService
from sheetmind.services.conversations import ConversationService
from sheetmind.services.projects import ProjectService
from sheetmind.services.excel import ExcelService
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
    assert first.status == "draft"
    assert tasks.update_status(first.task_id, "running")
    assert tasks.get_task(first.task_id).status == "running"
    assert tasks.update_status(first.task_id, "completed")
    assert tasks.get_task(first.task_id).status == "completed"
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
    projects = ProjectService(claim_legacy_projects=True)
    legacy_project = projects.create_project("Legacy", [])
    session = AnonymousSessionService().resolve(None)

    claimed = projects.list_projects(session.session_id)

    assert [item.project_id for item in claimed] == [legacy_project]
    assert projects.get_project(legacy_project, session.session_id) is not None


def test_legacy_projects_are_not_exposed_by_default(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    projects = ProjectService()
    legacy_project = projects.create_project("Legacy", [])
    session = AnonymousSessionService().resolve(None)

    assert projects.list_projects(session.session_id) == []
    assert projects.get_project(legacy_project, session.session_id) is None


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

    claimed = ProjectService(claim_legacy_projects=True).list_projects(session.session_id)

    assert [(item.project_id, item.project_name) for item in claimed] == [
        ("newer", "我的项目"),
        ("older", "我的项目 (1)"),
    ]


def test_expired_session_deletes_its_entire_workspace(tmp_path, monkeypatch):
    path = isolated_database(tmp_path, monkeypatch)
    sessions = AnonymousSessionService(lifetime_days=7)
    session = sessions.resolve(None)
    projects = ProjectService()
    project_id = projects.create_project("Expiring", [], session.session_id)
    task = TaskService().create_task(project_id)
    ConversationService().add_message(task.task_id, "user", "test question")

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO files (file_id, project_id, file_name, file_data, content_sha256)
            VALUES ('file-1', ?, 'test.xlsx', X'01', 'digest')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO analysis_contexts (task_id, context_json)
            VALUES (?, '{}')
            """,
            (task.task_id,),
        )
        conn.execute(
            "UPDATE anonymous_sessions SET expires_at = '2000-01-01T00:00:00+00:00' "
            "WHERE session_id = ?",
            (session.session_id,),
        )

    replacement = sessions.resolve(session.session_id)

    assert replacement.session_id != session.session_id
    with sqlite3.connect(path) as conn:
        for table in ("projects", "files", "tasks", "conversations", "analysis_contexts"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_every_same_name_upload_gets_a_visible_numeric_suffix(tmp_path, monkeypatch):
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
    [identical_copy] = projects.add_files_to_project(
        project_id, [upload(b"version-one")], session.session_id
    )
    [second_version] = projects.add_files_to_project(
        project_id, [upload(b"version-two")], session.session_id
    )

    assert created.status == "created"
    assert identical_copy.status == "created"
    assert second_version.status == "created"
    assert len({created.file_id, identical_copy.file_id, second_version.file_id}) == 3
    assert [created.file_name, identical_copy.file_name, second_version.file_name] == [
        "storage.xlsx",
        "storage (1).xlsx",
        "storage (2).xlsx",
    ]
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            """
            SELECT file_id, file_data
            FROM files
            WHERE project_id = ?
            ORDER BY created_at ASC, file_id ASC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert {row[1] for row in rows} == {b"version-one", b"version-two"}
    assert ExcelService().get_file_by_id(project_id, created.file_id) == b"version-one"
    assert ExcelService().get_file_by_id(project_id, second_version.file_id) == b"version-two"


def test_sheet_preview_detects_header_and_formats_excel_month_serial(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    session = AnonymousSessionService().resolve(None)
    projects = ProjectService()
    project_id = projects.create_project("Preview", [], session.session_id)
    buffer = io.BytesIO()
    pd.DataFrame([
        [None, None, 300],
        ["月份", "平台", "费用金额"],
        [45992, "乐天", 100],
    ]).to_excel(buffer, sheet_name="仓储", header=False, index=False)
    upload = SimpleNamespace(
        fileName="仓储.xlsx",
        base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
    )
    [stored] = projects.add_files_to_project(
        project_id, [upload], session.session_id
    )

    parsed = ExcelService().parse_excel(
        project_id, stored.file_name, file_id=stored.file_id
    )
    warehouse = parsed["sheets"][0]

    assert warehouse["columns"] == ["月份", "平台", "费用金额"]
    assert warehouse["preview"][0] == {
        "月份": "2025-12",
        "平台": "乐天",
        "费用金额": 100,
    }


def test_dataframe_loader_can_union_changed_same_name_workbooks_by_file_id(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    session = AnonymousSessionService().resolve(None)
    projects = ProjectService()
    project_id = projects.create_project("Cross workbook", [], session.session_id)

    def upload(amount: int):
        buffer = io.BytesIO()
        pd.DataFrame({"SKU": [f"SKU-{amount}"], "费用": [amount]}).to_excel(
            buffer, sheet_name="尾程", index=False
        )
        return SimpleNamespace(
            fileName="12月尾程仓储汇总.xlsx",
            base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
        )

    [first] = projects.add_files_to_project(
        project_id, [upload(100)], session.session_id
    )
    [second] = projects.add_files_to_project(
        project_id, [upload(200)], session.session_id
    )
    ctx = AnalysisContext(project_id=project_id, task_id="task-1")
    ctx.requested_sheet_scope = [
        {
            "fileId": first.file_id,
            "fileName": first.file_name,
            "sheets": ["尾程"],
        },
        {
            "fileId": second.file_id,
            "fileName": second.file_name,
            "sheets": ["尾程"],
        },
    ]

    result = DataframeLoaderTool().run(
        ctx,
        selected_files=[
            {
                "fileId": first.file_id,
                "fileName": first.file_name,
                "sheets": ["尾程"],
            },
            {
                "fileId": second.file_id,
                "fileName": second.file_name,
                "sheets": ["尾程"],
            },
        ],
        merge_strategy="union",
    )

    assert sorted(result["费用"].tolist()) == [100, 200]
    assert result["__source_file"].nunique() == 2


def test_analysis_context_is_restored_after_memory_store_restart(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    projects = ProjectService()
    tasks = TaskService()
    project_id = projects.create_project("Persistent analysis", [])
    task = tasks.create_task(project_id)
    first_store = ContextStore(persist=True)
    ctx = first_store.get_or_create(task.task_id, project_id)
    ctx.requested_sheet_scope = [{"fileName": "costs.xlsx", "sheets": ["尾程"]}]
    ctx.add_user_turn("哪个店铺物流费用最高")
    ctx.add_assistant_turn(
        "乐天最高",
        ResultBlocks(blocks=[SummaryBlock(content="乐天最高")]),
    )
    ctx.active_lineage = ResultLineage(filters={"店铺": ["乐天"]})
    first_store.set(task.task_id, ctx)

    restored = ContextStore(persist=True).get_or_create(task.task_id, project_id)

    assert restored.conversation[-1].content == "乐天最高"
    assert restored.active_result.all_summaries() == ["乐天最高"]
    assert restored.requested_sheet_scope == [
        {"fileName": "costs.xlsx", "sheets": ["尾程"]}
    ]
    assert restored.active_lineage.filters == {"店铺": ["乐天"]}


def test_conversation_history_persists_structured_analysis_result(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    project_id = ProjectService().create_project("Conversation history", [])
    task = TaskService().create_task(project_id)
    conversations = ConversationService()
    result = ResultBlocks(blocks=[SummaryBlock(content="乐天最高")])

    conversations.add_message(task.task_id, "user", "哪个店铺最高")
    conversations.add_message(
        task.task_id,
        "assistant",
        "乐天最高",
        metadata={"result": result.model_dump()},
    )

    history = conversations.get_conversation_history(task.task_id)

    assert history[0]["metadata"] is None
    assert history[1]["metadata"]["result"]["type"] == "result_blocks"
    assert history[1]["metadata"]["result"]["blocks"][0]["content"] == "乐天最高"


def test_analysis_artifact_preserves_full_result_and_exports_excel(tmp_path, monkeypatch):
    isolated_database(tmp_path, monkeypatch)
    project_id = ProjectService().create_project("Full result artifact", [])
    task = TaskService().create_task(project_id)
    original = pd.DataFrame({
        "易仓SKU": [f"SKU-{index:04d}" for index in range(1205)],
        "物流费用": [float(index) for index in range(1205)],
    })
    store = AnalysisArtifactStore()

    artifact_id = store.save(task.task_id, "q1", original)
    restored = store.load(artifact_id, task.task_id)
    workbook = pd.read_excel(io.BytesIO(store.to_excel(artifact_id, task.task_id)))

    assert artifact_id
    pd.testing.assert_frame_equal(restored, original)
    pd.testing.assert_frame_equal(workbook, original, check_dtype=False)


def test_refresh_followup_rehydrates_the_focused_question_artifact(tmp_path, monkeypatch):
    from sheetmind.analysis.agent import SheetMindAgent

    isolated_database(tmp_path, monkeypatch)
    project_id = ProjectService().create_project("Focused follow-up", [])
    task = TaskService().create_task(project_id)
    store = AnalysisArtifactStore()
    tail_df = pd.DataFrame({"物流商": ["日本海外仓"], "费用金额": [300.0]})
    storage_df = pd.DataFrame({"平台": ["亚马逊", "乐天"], "仓储费": [999.0, 300.0]})
    tail_id = store.save(task.task_id, "q1", tail_df)
    storage_id = store.save(task.task_id, "q2", storage_df)
    tail_table = TableBlock(
        columns=list(tail_df.columns),
        rows=tail_df.to_dict("records"),
        artifact_id=tail_id,
    )
    storage_table = TableBlock(
        columns=list(storage_df.columns),
        rows=storage_df.head(1).to_dict("records"),
        total_rows=len(storage_df),
        artifact_id=storage_id,
    )
    ctx = AnalysisContext(project_id=project_id, task_id=task.task_id)
    ctx.active_result = ResultBlocks(
        focus_question_id="q2",
        questions=[
            QuestionResult(
                question_id="q1", query="尾程", status="success", blocks=[tail_table]
            ),
            QuestionResult(
                question_id="q2", query="仓储", status="success", blocks=[storage_table]
            ),
        ],
        blocks=[tail_table, storage_table],
    )

    restored = SheetMindAgent._previous_result_dataframe(ctx)

    pd.testing.assert_frame_equal(restored, storage_df)
