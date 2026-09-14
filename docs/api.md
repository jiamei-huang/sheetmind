# HTTP API

The API base URL is `http://127.0.0.1:8000/api` by default.

All browser requests use the HTTP-only `sheetmind_anonymous_session` cookie. The server creates it automatically, renews it for `ANONYMOUS_SESSION_TTL_DAYS`, and scopes every project, task, file, analysis, and conversation operation to that anonymous workspace. Browser clients must enable credentials. Previously unowned projects remain hidden unless the one-time local migration option `SHEETMIND_CLAIM_LEGACY_PROJECTS=true` is enabled.

## Anonymous session

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/session` | Return anonymous status, expiry time, and configured retention days |
| `DELETE` | `/session` | Delete the current anonymous workspace and expire its cookie |

Deleting or expiring a session cascades to its projects, uploaded workbooks, tasks, conversations, and persisted analysis contexts. The next request receives a fresh anonymous session.

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

Each upload creates a new workbook record with `uploadStatus: created`. Repeated names receive numeric suffixes such as `report (1).xlsx`, while `fileId` remains the stable identity used for previews and analysis. Uploads accept valid `.xlsx` and `.xls` workbooks. Other extensions, malformed base64, and invalid workbook contents return HTTP 400 before any file is stored.

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

ResultBlocks also includes `run_id`, an overall `status`, and a `questions` array. Each question owns its query, status, blocks, and executor-produced provenance. The flat `blocks` array is retained for clients that render all tables/charts as tabs. Computed table blocks include an `artifact_id`, `total_rows`, and `preview_row_count`; `rows` is only the bounded browser preview.

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
  "message": "当前勾选范围内没有合适的数据源。“sales.xlsx / Sheet2”更匹配这个问题。请先勾选该 Sheet，再重新提交问题。",
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

`selectedFiles` is the complete checked scope for that request. An empty list clears any previous scope. Clients must ask the user to update the checkboxes and resubmit; query text and candidate IDs cannot authorize an unchecked source. The server enforces this once during source binding and again immediately before loading workbook bytes.

If multiple checked workbook/Sheet pairs remain equally plausible, the response uses `needs_clarification` and includes each candidate's `file_id`, `file_name`, and `sheet_name`. Clients should show those identities without treating them as one-click authorization controls.

`progress` events include a stable `step_id` when the runtime enters a pipeline stage. Values are `routing`, `query_planning`, `sheet_selection`, `data_loading`, `semantic_typing`, `data_profiling`, `execution`, `chart_planning`, `insight_writing`, and `validation`; clients should use the message for display and the ID for state tracking.

Chart blocks may include optional `confidence` and `reason` fields. A response may contain multiple table or chart blocks for independent query branches; their `title` fields identify the corresponding result tab. Invalid chart data is omitted during validation so valid table and summary blocks can still be rendered. Clients should classify an analysis from `output_intents` and render the block kinds actually returned instead of maintaining a second prompt-keyword classifier.

`POST /analysis` executes the same flow and returns ResultBlocks as a normal JSON response.

`GET /analysis/artifacts/{artifact_id}/excel?taskId=...` exports the complete computed dataframe as `.xlsx`. The artifact must belong to the requested task and the task must belong to the current anonymous session.
