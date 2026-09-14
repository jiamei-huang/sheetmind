# Development

## Requirements

- Python 3.9 or newer
- Node.js 20 or newer
- npm 10 or newer

## Server

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python3 -m sheetmind
```

The local database defaults to `server/data/sheetmind.db`. Override it with `SHEETMIND_DB_PATH` when tests or deployments need isolated state.

Anonymous workspaces use a sliding inactivity window. Keep the local default for debugging:

```bash
ANONYMOUS_SESSION_TTL_DAYS=30
```

Set `ANONYMOUS_SESSION_TTL_DAYS=7` in the public Demo environment. Expired sessions are deleted with their projects, uploaded workbooks, tasks, conversations, and analysis contexts on the next API request. Deploy the Demo with a fresh database. If a local database contains projects created before session isolation, set `SHEETMIND_CLAIM_LEGACY_PROJECTS=true` for one local migration run only; never enable it in the public Demo.

Set `SHEETMIND_SESSION_COOKIE_SECURE=true` when the public Demo is served over HTTPS.

Run tests and routing evaluations:

```bash
python3 -m pytest
python3 -m sheetmind.analysis.evals.runner --routing-only
```

## Web

```bash
cd web
npm install
npm run dev
```

Set `VITE_API_BASE_URL` only when the API is not running at `http://127.0.0.1:8000`. Do not include `/api` in the value.

Production build:

```bash
npm run build
```

## Model configuration

The runtime uses an OpenAI-compatible client. `OPENAI_API_KEY` and `OPENAI_BASE_URL` configure the connection. Role-specific `SHEETMIND_MODEL_*_ID` variables allow cheaper models for routing and query planning while reserving stronger models for generated code. `SHEETMIND_MODEL_QUERY_PLANNING_ID` controls semantic structure detection and dependency-aware decomposition. `SHEETMIND_MODEL_ROUTING_ID` only supplements low-confidence atomic operation/output intents; deterministic local rules still choose the execution route.

Routing vocabulary is configured in `server/sheetmind/analysis/config/routing_rules.json`. Add data work terms under `level_2.operation_terms` and presentation terms under `level_2.output_terms`; do not add execution-engine keyword groups. Derived intent and Route behavior belong to the signal-combination rules in `RoutingClassificationSkill`, while result-block behavior belongs to `OutputPlanningSkill`. Every new ambiguity rule must include a positive example and a nearby negative example in routing tests.

Never commit `.env`, local databases, uploaded workbooks, logs, or generated build output.
