"""HTTP regressions for anonymous workspace isolation."""

import asyncio

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
