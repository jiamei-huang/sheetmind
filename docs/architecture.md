# Architecture

SheetMind is a two-process application with one public product surface: the Excel analysis workspace.

```text
Browser (React)
  -> FastAPI routes
    -> project, file, task, and conversation services
    -> SheetMindAgent
      -> deterministic query normalization
      -> hybrid structure detection and dependency-aware query planning
      -> operation/output intent extraction and deterministic route decision
      -> sheet-selection and semantic field skills
      -> dataframe tools and guarded Python execution
      -> output planning, chart, and insight skills
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

Model access is replaceable through `ModelRouter` and `ModelProvider`. Generated Python runs in a subprocess with a timeout, resource caps, forbidden-operation scan, and output contract validation.

## Analysis routing

Intent handling is split into five decisions:

1. **Query Normalization** deterministically preserves the original query while producing a machine-oriented version. It normalizes Unicode punctuation and whitespace, converts contextual Chinese numerals, and expands relative dates into explicit half-open ranges. It does not use an LLM.
2. **Structure Detection** uses high-precision rules for explicit sequencing, parallel questions, dependencies, and clearly simple queries. Complex or ambiguous shapes are sent to `QueryPlanningSkill`, whose model may confirm one atomic operation or return multiple dependency-ordered steps.
3. **Intent Extraction** independently produces `OperationIntent` and `OutputIntent`. Operation terms describe work such as filtering, aggregation, trend calculation, and anomaly detection. Output terms describe presentation such as a table, chart, insight, or Excel export. Neither vocabulary names an execution engine.
4. **Route Decision** uses only `OperationIntent`. Computation operations select `CODE_GEN`; filter/sort/pass-through operations select `RULE_ENGINE`; explanation without new computation selects `INSIGHT_ONLY`. A low-confidence atomic query may ask the routing model for additional operation and output intents, but the model cannot directly select or override the route.
5. **Output Planning** runs after execution planning and uses `OutputIntent` to decide which result blocks to assemble. For example, an explicit chart request may return a chart and summary without a redundant table, while Excel export preserves tabular output.

This keeps explicit simple queries on a zero-model fast path while using semantic understanding where a mistaken structural guess would be costly. Every planned atomic step crosses the same signal-extraction and route-decision interface.

The available execution routes are:

| Routing hint | Meaning | New structured computation |
|---|---|---|
| `RULE_ENGINE` / `rule` | Deterministic filter, sort, date filtering, pass-through, or simple extrema. | Yes |
| `CODE_GEN` / `code` | LLM generates pandas data-processing code for aggregation, pivoting, Top-N, growth rate, share calculation, or complex conditions. | Yes |
| `INSIGHT_ONLY` / `insight` | LLM writes insight from existing results or lightweight dataframe context. It does not create a new table/chart computation. | No |

The routing result exposes both intent objects and execution metadata for downstream planning and tracing:

- `operation_intent.types`: work labels such as `filter`, `sort`, `aggregate`, `trend`, `compare`, `anomaly_detect`, and `explain`.
- `output_intent.formats`: presentation labels such as `auto`, `table`, `chart`, `insight`, and `export_excel`.
- `needs_new_computation`: whether this turn should produce a new structured result.
- `uses_previous_result`: whether the turn is a follow-up that refers to prior results.
- `target_fields`: best-effort field mentions before semantic typing runs.

`QueryPlanningSkill` returns one or more ordered `ExecutionStep` records containing original and normalized query text, a route, dependency IDs, input source, operation intents, output intents, fields, and confidence. Dependencies may only reference prior steps, and planning output is rejected if it drops currency qualifiers or numeric constraints. Invalid model output falls back to deterministic clause splitting.

`SheetMindAgent` stores the resulting `ExecutionPlan` in runtime context and trace. Dependent steps consume the declared prior result; independent steps consume the original source and may produce multiple named result blocks. Semantic typing, profiling, field resolution, code-generation field contracts, and executor validation are applied to every computational step.

## Semantic data contract

`SemanticTypingSkill` produces field metadata that is reused by profiling, rule execution, code generation, and chart planning:

- Type: `numeric`, `datetime`, `datetime-like`, `categorical`, `identifier`, or `unknown`.
- Role, aggregation safety, aliases, qualifiers, and confidence.
- Identifier columns such as SKU, order number, and customer ID are never treated as summable metrics.
- Qualified aliases prioritize fields such as `金额（RMB）` when a query mentions `人民币金额`.

`DataProfilingSkill` returns a structured profile and a compact prompt view. Profiles include null and cardinality ratios, samples, numeric statistics, date ranges, candidate metrics/dimensions/dates, and recommended aggregations.

## Execution and presentation safeguards

`DataframeLoaderTool` can emit a load report with sources, detected headers, cleanup counts, and warnings. `RuleEngineTool` emits a structured rule report to the agent. `RuleResultValidator` checks operation coverage, required qualified columns, pass-through equivalence, row-count constraints, sort order, and requested date periods before the result is accepted. A rejected rule result changes the step and top-level execution route to `CODE_GEN`, then enters `RepairLoop`.

`RepairLoop` wraps `CodeGenerationSkill` and `PythonExecutorTool`. It permits the initial attempt plus at most two repairs, with a 120-second repair budget. It stops early when generated code repeats, generation/execution/field-contract errors repeat, or executor safety policy rejects the code.

`ChartPlanningSkill` uses semantic metadata to choose axes, supports period labels as time axes, limits crowded categorical charts with `Other`, and records a confidence and reason. `ResultValidator` enforces frontend table/chart caps and drops an invalid chart while retaining valid table and summary blocks.

Insights receive computed facts rather than raw prompt-only context. SSE progress frames carry stable step IDs: `routing`, `query_planning`, `sheet_selection`, `data_loading`, `semantic_typing`, `data_profiling`, `execution`, `chart_planning`, `insight_writing`, and `validation`.

## Result protocol

Every completed analysis returns:

```json
{
  "type": "result_blocks",
  "output_intents": ["table", "chart"],
  "blocks": [
    { "kind": "metric", "label": "Revenue", "value": 1280000, "unit": "CNY" },
    { "kind": "table", "columns": ["region", "sales"], "rows": [] },
    { "kind": "chart", "chart_type": "bar", "labels": [], "series": [] },
    { "kind": "summary", "content": "Key findings" }
  ]
}
```

Blocks may be combined. `output_intents` records the requested presentation contract; the web application uses it for result classification and uses the actual block kinds to render available panels.

## State

- SQLite stores projects, uploaded workbooks, tasks, and conversation messages.
- `ContextStore` keeps active multi-turn analysis context in process memory.
- `TraceStore` keeps execution traces in process memory.
- `server/data` and `server/logs` are runtime-only directories.
