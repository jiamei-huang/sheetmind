# Architecture

SheetMind is a two-process application with one public product surface: the Excel analysis workspace.

```text
Browser (React)
  -> FastAPI routes
    -> project, file, task, and conversation services
    -> SheetMindAgent
      -> two-level routing and dependency-aware query planning
      -> sheet-selection and semantic field skills
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

Model access is replaceable through `ModelRouter` and `ModelProvider`. Generated Python runs in a subprocess with a timeout, resource caps, forbidden-operation scan, and output contract validation.

## Analysis routing

Intent handling has two rule levels configured in `server/sheetmind/analysis/config/routing_rules.json`:

- Level 1 identifies multi-turn mode and structural signals such as sequential steps, parallel questions, and dependencies.
- Level 2 classifies an atomic operation by execution route and produces operation facets.

Simple queries remain on the rule-first fast path. When Level 1 detects a multi-operation query, `QueryPlanningSkill` asks the planning model to decompose it into validated atomic steps. Each step is then classified independently by Level 2; low-confidence atomic routes can still use the routing model fallback.

The available execution routes are:

| Routing hint | Meaning | New structured computation |
|---|---|---|
| `RULE_ENGINE` / `rule` | Deterministic filter, sort, date filtering, pass-through, or simple extrema. | Yes |
| `CODE_GEN` / `code` | LLM generates pandas data-processing code for aggregation, pivoting, Top-N, growth rate, share calculation, or complex conditions. | Yes |
| `INSIGHT_ONLY` / `insight` | LLM writes insight from existing results or lightweight dataframe context. It does not create a new table/chart computation. | No |

The same skill also emits secondary routing facets for debugging and future Studio views:

- `operation_types`: product intent labels such as `filter`, `sort`, `aggregate`, `trend`, `compare`, `anomaly`, `explain`, and `chart`.
- `needs_new_computation`: whether this turn should produce a new structured result.
- `wants_chart`: whether the user explicitly requested a chart or visualization.
- `uses_previous_result`: whether the turn is a follow-up that refers to prior results.
- `target_fields`: best-effort field mentions before semantic typing runs.

`QueryPlanningSkill` returns ordered `ExecutionStep` records containing a route, dependency IDs, input source, operations, chart intent, fields, and confidence. Dependencies may only reference prior steps, and planning output is rejected if it drops currency qualifiers or numeric constraints. Invalid model output falls back to deterministic clause splitting.

`SheetMindAgent` stores the resulting `ExecutionPlan` in runtime context and trace. Dependent steps consume the declared prior result; independent steps consume the original source and may produce multiple named result blocks. Semantic typing, profiling, field resolution, code-generation field contracts, and executor validation are applied to every computational step.

## Semantic data contract

`SemanticTypingSkill` produces field metadata that is reused by profiling, rule execution, code generation, and chart planning:

- Type: `numeric`, `datetime`, `datetime-like`, `categorical`, `identifier`, or `unknown`.
- Role, aggregation safety, aliases, qualifiers, and confidence.
- Identifier columns such as SKU, order number, and customer ID are never treated as summable metrics.
- Qualified aliases prioritize fields such as `金额（RMB）` when a query mentions `人民币金额`.

`DataProfilingSkill` returns a structured profile and a compact prompt view. Profiles include null and cardinality ratios, samples, numeric statistics, date ranges, candidate metrics/dimensions/dates, and recommended aggregations.

## Execution and presentation safeguards

`DataframeLoaderTool` can emit a load report with sources, detected headers, cleanup counts, and warnings. `RuleEngineTool` can emit a structured rule report while preserving its DataFrame API for callers.

`ChartPlanningSkill` uses semantic metadata to choose axes, supports period labels as time axes, limits crowded categorical charts with `Other`, and records a confidence and reason. `ResultValidator` enforces frontend table/chart caps and drops an invalid chart while retaining valid table and summary blocks.

Insights receive computed facts rather than raw prompt-only context. SSE progress frames carry stable step IDs: `routing`, `query_planning`, `sheet_selection`, `data_loading`, `semantic_typing`, `data_profiling`, `execution`, `chart_planning`, `insight_writing`, and `validation`.

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
