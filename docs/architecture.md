# Architecture

SheetMind is a two-process application with one public product surface: the Excel analysis workspace.

```text
Browser (React)
  -> FastAPI routes
    -> project, file, task, and conversation services
    -> SheetMindAgent
      -> routing and sheet-selection skills
      -> dataframe tools and guarded Python execution
      -> chart and insight skills
      -> ResultBlocks
```

## Repository modules

### `web`

The web application owns browser state, uploads, task interaction, streaming progress, and result rendering. Calls to the server are centralized in `web/src/api`.

The server response is the native `ResultBlocks` protocol. `resultBlocks.js` converts that transport model into a UI-oriented view model; no historical server response shape is supported.

### `server/sheetmind/api`

FastAPI route modules validate transport data and coordinate application services. They do not contain dataframe or model logic.

### `server/sheetmind/services`

Services own project, task, conversation, and Excel persistence behavior. SQLite is local runtime state and is not committed.

### `server/sheetmind/analysis`

`SheetMindAgent.run(context, query, emitter)` is the analysis interface. It hides routing, dataframe loading, generated-code repair, validation, chart planning, insight generation, and tracing behind one call.

Model access is replaceable through `ModelRouter` and `ModelProvider`. Generated Python runs in a subprocess with a timeout and import allowlist.

## Result protocol

Every completed analysis returns:

```json
{
  "type": "result_blocks",
  "blocks": [
    { "kind": "metric", "label": "Revenue", "value": 1280000, "unit": "CNY" },
    { "kind": "table", "columns": ["region", "sales"], "rows": [] },
    { "kind": "chart", "chart_type": "bar", "labels": [], "series": [] },
    { "kind": "summary", "content": "Key findings" }
  ]
}
```

Blocks may be combined. The web application decides which result panels to show from the block kinds present.

## State

- SQLite stores projects, uploaded workbooks, tasks, and conversation messages.
- `ContextStore` keeps active multi-turn analysis context in process memory.
- `TraceStore` keeps execution traces in process memory.
- `server/data` and `server/logs` are runtime-only directories.
