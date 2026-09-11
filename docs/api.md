# HTTP API

The API base URL is `http://127.0.0.1:8000/api` by default.

All browser requests use the HTTP-only `sheetmind_anonymous_session` cookie. The server creates it automatically, renews it for 30 days, and scopes every project, task, file, analysis, and conversation operation to that anonymous workspace. Browser clients must enable credentials. Existing databases are migrated by assigning previously unowned projects to the first anonymous session that lists projects.

## Projects

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects` | List projects |
| `POST` | `/projects` | Get or create a same-name project in the current anonymous session |
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

Each returned file includes `uploadStatus`: `created` for a new file, `reused` when the same name and content already exist, or `replaced` when the same name contains changed content. `reused` does not create another database record; `replaced` updates the existing record and keeps its `fileId`.

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
{
  "taskId": "uuid",
  "query": "Show sales by region",
  "selectedFiles": [
    {"fileName": "sales.xlsx", "sheets": ["Sheet1"]}
  ]
}
```

The response uses Server-Sent Events with `thinking`, `progress`, `repairing`, `done`, and `error` event payloads. The `done.result` field contains native ResultBlocks. Its top-level `output_intents` array records the requested presentation contract (`auto`, `table`, `chart`, `insight`, or `export_excel`) independently from execution routing.

When a query refers to multiple equally plausible columns, analysis returns a `field_resolution` block instead of running a calculation with an arbitrary field:

```json
{
  "kind": "field_resolution",
  "status": "needs_clarification",
  "reference": "金额",
  "selected_column": null,
  "confidence": 0.5,
  "reason": "多个候选字段的匹配程度接近，自动选择可能改变分析结果",
  "message": "“金额”可能对应多个字段，请选择后继续。",
  "candidates": [
    {"column": "金额", "confidence": 0.98, "reason": "字段名称出现在查询中"},
    {"column": "金额（RMB）", "confidence": 0.98, "reason": "字段名称出现在查询中"}
  ]
}
```

An `assumed` block accompanies a completed result when one partial field match is usable but should be disclosed. A `confirmed` field is recorded in the execution plan and does not add a user-visible block.

When the selected sheet conflicts with the query, or multiple sheets remain plausible after metadata and semantic ranking, analysis stops before loading data and returns a `sheet_resolution` block:

```json
{
  "kind": "sheet_resolution",
  "status": "scope_conflict",
  "message": "当前选择的工作表与问题不一致；“Sheet2”中的字段更匹配。请选择是否切换后继续。",
  "current_sheets": ["Sheet1"],
  "candidates": [
    {
      "candidate_id": "sales.xlsx::Sheet2",
      "file_name": "sales.xlsx",
      "sheet_name": "Sheet2",
      "columns": ["店铺", "金额（RMB）"],
      "confidence": 0.91,
      "reason": "planned fields match ['金额（RMB）']"
    }
  ]
}
```

Clients should resubmit the original question with the exact selected workbook and sheet. The web client does this through its candidate buttons. The server never permits the sheet-selection model to introduce a workbook or sheet outside the recalled candidates.

`progress` events include a stable `step_id` when the runtime enters a pipeline stage. Values are `routing`, `query_planning`, `sheet_selection`, `data_loading`, `semantic_typing`, `data_profiling`, `execution`, `chart_planning`, `insight_writing`, and `validation`; clients should use the message for display and the ID for state tracking.

Chart blocks may include optional `confidence` and `reason` fields. A response may contain multiple table or chart blocks for independent query branches; their `title` fields identify the corresponding result tab. Invalid chart data is omitted during validation so valid table and summary blocks can still be rendered. Clients should classify an analysis from `output_intents` and render the block kinds actually returned instead of maintaining a second prompt-keyword classifier.

`POST /analysis` executes the same flow and returns ResultBlocks as a normal JSON response.
