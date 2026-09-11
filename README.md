# SheetMind

SheetMind is an Excel analysis application for persistent projects, multi-turn questions, structured charts, and reproducible execution traces.

The local web app uses a 30-day anonymous browser session. Projects, tasks, uploaded workbooks, and conversations are restored after refresh or server restart without requiring an account or another upload. Re-uploading identical file content is idempotent; changed content with the same file name replaces the stored workbook.

## Analysis runtime

The backend first normalizes query syntax, classifies query structure, then builds
a dependency-aware execution plan. High-confidence single questions stay on the
rule fast path; complex or ambiguous questions use semantic planning to confirm
one operation or decompose multiple operations and their dependencies. Each
atomic step independently extracts operation and output intents. Deterministic
operation rules select guarded rules, generated pandas code, or insight writing;
output planning separately selects table, chart, insight, or export-ready results.
Sheet selection combines workbook metadata with constrained model ranking and
asks before leaving a user-selected sheet. Semantic typing combines physical
column profiles, deterministic rules, and validated model refinement for
ambiguous fields. It recognizes period labels and identifier columns, prefers
qualified metrics such as RMB amounts, and returns validated table, chart, metric, and summary
blocks. Independent branches can return multiple named tables or charts, which
the web workspace exposes as tabs. Invalid chart data degrades to the remaining
safe result blocks.

## Repository layout

```text
.
├── web/       Vite + React user interface
├── server/    FastAPI application and analysis runtime
└── docs/      Architecture, API, and development guides
```

## Quick start

Start the API:

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python3 -m sheetmind
```

Start the web application in another terminal:

```bash
cd web
npm install
npm run dev
```

Open [http://localhost:3002](http://localhost:3002). The API runs at [http://127.0.0.1:8000](http://127.0.0.1:8000), with OpenAPI documentation at `/docs`.

## Verification

```bash
cd server && python3 -m pytest -q
cd server && python3 -m sheetmind.analysis.evals.runner --routing-only
cd web && npm test
cd web && npm run build
```

See [docs/development.md](docs/development.md) for configuration, [docs/architecture.md](docs/architecture.md) for the system design, and [docs/api.md](docs/api.md) for the HTTP protocol.
