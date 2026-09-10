# HTTP API

The API base URL is `http://127.0.0.1:8000/api` by default.

## Projects

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects` | List projects |
| `POST` | `/projects` | Create a project and its first task |
| `PATCH` | `/projects/{project_id}` | Rename a project |
| `DELETE` | `/projects/{project_id}` | Delete a project and related state |

## Files

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/files/project/{project_id}` | List uploaded workbooks |
| `POST` | `/files` | Upload workbooks to a project |
| `POST` | `/files/preview` | Return Sheet names and preview rows |
| `DELETE` | `/files/project/{project_id}/{file_name}` | Delete a workbook |

Uploads use JSON with base64 file content to preserve the current browser workflow.

## Tasks and conversations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/tasks?projectId=...` | List project tasks |
| `POST` | `/tasks` | Create a task |
| `PATCH` | `/tasks/{task_id}` | Rename a task |
| `DELETE` | `/tasks/{task_id}` | Delete a task |
| `GET` | `/conversations/{task_id}` | Read persisted messages |
| `DELETE` | `/conversations/{task_id}` | Clear persisted and active context |

## Analysis

`POST /analysis/stream` is the browser endpoint. The request is:

```json
{ "taskId": "uuid", "query": "Show sales by region" }
```

The response uses Server-Sent Events with `thinking`, `progress`, `repairing`, `done`, and `error` event payloads. The `done.result` field contains native ResultBlocks. Its top-level `output_intents` array records the requested presentation contract (`auto`, `table`, `chart`, `insight`, or `export_excel`) independently from execution routing.

`progress` events include a stable `step_id` when the runtime enters a pipeline stage. Values are `routing`, `query_planning`, `sheet_selection`, `data_loading`, `semantic_typing`, `data_profiling`, `execution`, `chart_planning`, `insight_writing`, and `validation`; clients should use the message for display and the ID for state tracking.

Chart blocks may include optional `confidence` and `reason` fields. A response may contain multiple table or chart blocks for independent query branches; their `title` fields identify the corresponding result tab. Invalid chart data is omitted during validation so valid table and summary blocks can still be rendered. Clients should classify an analysis from `output_intents` and render the block kinds actually returned instead of maintaining a second prompt-keyword classifier.

`POST /analysis` executes the same flow and returns ResultBlocks as a normal JSON response.
