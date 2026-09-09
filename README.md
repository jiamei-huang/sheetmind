# SheetMind

SheetMind is an Excel analysis application for persistent projects, multi-turn questions, structured charts, and reproducible execution traces.

## Analysis runtime

The backend first classifies query structure, then builds a dependency-aware
execution plan. Single questions stay on the fast path; multi-operation questions
are decomposed and each step independently selects deterministic rules, guarded
generated pandas code, or insight writing.
It recognizes period labels and identifier columns, prefers qualified metrics
such as RMB amounts, and returns validated table, chart, metric, and summary
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
