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

The runtime uses an OpenAI-compatible client. `OPENAI_API_KEY` and `OPENAI_BASE_URL` configure the connection. Role-specific `SHEETMIND_MODEL_*_ID` variables allow cheaper models for routing and stronger models for generated code.

Never commit `.env`, local databases, uploaded workbooks, logs, or generated build output.
