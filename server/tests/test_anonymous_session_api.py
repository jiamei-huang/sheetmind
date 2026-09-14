"""HTTP regressions for anonymous workspace isolation."""

import asyncio
import base64

import httpx


MODEL_ENV_KEYS = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "SHEETMIND_MODEL_ROUTING_ID",
    "SHEETMIND_MODEL_QUERY_PLANNING_ID",
    "SHEETMIND_MODEL_SEMANTIC_TYPING_ID",
    "SHEETMIND_MODEL_SHEET_SELECTION_ID",
    "SHEETMIND_MODEL_CODE_GENERATION_ID",
    "SHEETMIND_MODEL_CODE_REPAIR_ID",
    "SHEETMIND_MODEL_INSIGHT_WRITING_ID",
    "SHEETMIND_MODEL_LONG_CONTEXT_ID",
]


def prepare_application_import(monkeypatch) -> None:
    for key in MODEL_ENV_KEYS:
        monkeypatch.setenv(key, "")


def run_async(coroutine) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coroutine)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_anonymous_cookie_restores_workspace_and_isolates_other_clients(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SHEETMIND_DB_PATH", str(tmp_path / "sheetmind-api.db"))
    prepare_application_import(monkeypatch)

    from sheetmind.application import create_app

    async def scenario():
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as first:
            initial = await first.get("/api/projects")
            assert initial.status_code == 200
            assert initial.json() == {"projects": []}
            assert "sheetmind_anonymous_session" in first.cookies

            created = await first.post(
                "/api/projects",
                json={"projectName": "Default Project", "files": []},
            )
            assert created.status_code == 201
            project_id = created.json()["projectId"]

            restored = await first.get("/api/projects")
            assert [item["projectId"] for item in restored.json()["projects"]] == [
                project_id
            ]

        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as second:
            isolated = await second.get("/api/projects")
            assert isolated.status_code == 200
            assert isolated.json() == {"projects": []}
            hidden = await second.get(f"/api/files/project/{project_id}")
            assert hidden.status_code == 404

    run_async(scenario())


def test_create_project_is_idempotent_within_anonymous_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SHEETMIND_DB_PATH", str(tmp_path / "sheetmind-api.db"))
    prepare_application_import(monkeypatch)

    from sheetmind.application import create_app

    async def scenario():
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            first = await client.post(
                "/api/projects",
                json={"projectName": "Default Project", "files": []},
            )
            second = await client.post(
                "/api/projects",
                json={"projectName": "Default Project", "files": []},
            )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["projectId"] == second.json()["projectId"]
        assert first.json()["isExistingProject"] is False
        assert second.json()["isExistingProject"] is True

    run_async(scenario())


def test_unsupported_uploads_are_rejected_before_project_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("SHEETMIND_DB_PATH", str(tmp_path / "sheetmind-upload.db"))
    prepare_application_import(monkeypatch)

    from sheetmind.application import create_app

    async def scenario():
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            responses = []
            for file_name in ("notes.rtf", "macros.xlsm"):
                responses.append(await client.post(
                    "/api/projects",
                    json={
                        "projectName": "Invalid upload",
                        "files": [{
                            "fileName": file_name,
                            "base64": base64.b64encode(b"unsupported").decode("ascii"),
                        }],
                    },
                ))
            projects = await client.get("/api/projects")

        assert [response.status_code for response in responses] == [400, 400]
        assert all(".xlsx, .xls" in response.json()["detail"] for response in responses)
        assert projects.json() == {"projects": []}

    run_async(scenario())


def test_excel_extension_with_invalid_content_is_not_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("SHEETMIND_DB_PATH", str(tmp_path / "sheetmind-upload.db"))
    prepare_application_import(monkeypatch)

    from sheetmind.application import create_app

    async def scenario():
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            project = await client.post(
                "/api/projects",
                json={"projectName": "Existing", "files": []},
            )
            project_id = project.json()["projectId"]
            response = await client.post(
                "/api/files",
                json={
                    "projectId": project_id,
                    "files": [{
                        "fileName": "fake.xlsx",
                        "base64": base64.b64encode(b"plain text").decode("ascii"),
                    }],
                },
            )
            files = await client.get(f"/api/files/project/{project_id}")

        assert response.status_code == 400
        assert "valid Excel" in response.json()["detail"]
        assert files.json()["files"] == []

    run_async(scenario())


def test_anonymous_session_reports_retention_and_can_reset_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SHEETMIND_DB_PATH", str(tmp_path / "sheetmind-reset.db"))
    prepare_application_import(monkeypatch)

    from sheetmind.application import create_app

    async def scenario():
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as client:
            info = await client.get("/api/session")
            assert info.status_code == 200
            assert info.json()["anonymous"] is True
            assert info.json()["retentionDays"] >= 1
            first_cookie = client.cookies["sheetmind_anonymous_session"]

            created = await client.post(
                "/api/projects",
                json={"projectName": "Disposable", "files": []},
            )
            assert created.status_code == 201

            reset = await client.delete("/api/session")
            assert reset.status_code == 200
            assert reset.json() == {"success": True}
            assert "sheetmind_anonymous_session" not in client.cookies

            empty = await client.get("/api/projects")
            assert empty.json() == {"projects": []}
            assert client.cookies["sheetmind_anonymous_session"] != first_cookie

    run_async(scenario())
