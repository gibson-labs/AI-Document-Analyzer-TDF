## TDF Risk Analyzer (React UI + FastAPI wrapper) 

Primary UX: React web app that lets you upload documents, run FedEx/Weighted/Memo analysis, chat with RAG, and download the generated report as Markdown.

### Prereqs
- Python 3.10+
- Node 18+ (for the React frontend)
- `OPENAI_API_KEY` set (required for embeddings + LLM calls)
- AWS Textract env vars set (required for document extraction in the web app):
  - `AWS_REGION`
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`

### Run (Docker, recommended) 

Docker builds install dependencies from `requirements.docker.txt` (kept smaller and more stable for container builds).

```bash
docker build -t tdf-risk-analyzer .
docker run --rm -p 5003:8080 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e AWS_REGION="$AWS_REGION" \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  tdf-risk-analyzer
```

Alternative (recommended): `--env-file` to avoid exporting in your shell:
```bash
docker run --rm -p 5003:8080 --env-file .env tdf-risk-analyzer
```

### Deploy (GitHub Actions + self-hosted runner)

This repo includes `.github/workflows/deploy.yml` which deploys via `docker compose` on a self-hosted runner.

Required GitHub Secrets:
- `OPENAI_API_KEY`
- `AWS_REGION`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Optional GitHub Secrets:
- `TDF_DECISION_MATRIX_PATH` (e.g. `/app/files/Decision Matrix's.xlsx`)
- `TDF_SHARED_FILES_DIR` (e.g. `/app/files`)
- `TDF_SHARED_EXTRACTED_TEXT_DIR` (e.g. `/app/extracted_text`)

The workflow writes a local `.env` file on the runner and runs `docker compose up -d --build`.
You can inspect the runtime config template in `.env.example`.

Open `http://localhost:5003`.

Optional: provide a decision matrix for Weighted mode (and FedEx criteria references) by mounting a local file and setting `TDF_DECISION_MATRIX_PATH`:

```bash
docker run --rm -p 5003:8080 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e TDF_DECISION_MATRIX_PATH="/app/files/Decision Matrix's.xlsx" \
  -v "$(pwd)/files:/app/files" \
  tdf-risk-analyzer
```

Optional (team compatibility): also write uploaded docs and extracted text into repo-style folders:
- Upload mirror: `TDF_SHARED_FILES_DIR=/app/files`
- Extract mirror: `TDF_SHARED_EXTRACTED_TEXT_DIR=/app/extracted_text`

### Run (Local dev, no Docker)

Terminal 1 (API):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="YOUR_KEY"
export AWS_REGION="us-west-2"
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
python3 -m uvicorn server:app --reload --port 8000
```

Terminal 2 (React):
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` (Vite proxies `/api/*` → `http://localhost:8000`).

### CLI (still supported)

From the project root:
```bash
python3 file.py --mode fedex --docs_dir files --persist_dir chroma --company "Acme" --rebuild_index
python3 file.py --mode weighted --docs_dir files --persist_dir chroma --company "Acme"
python3 file.py --mode memo --docs_dir files --persist_dir chroma --company "Acme"
```

### Gradio UI (optional)

```bash
python3 app.py
```

### API Environment Variables

- `OPENAI_API_KEY`: required
- `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`: required for Textract extraction
- `TDF_DECISION_MATRIX_PATH`: path to a server-side `.xlsx` decision matrix (required for Weighted mode)
- `TDF_SESSIONS_ROOT`: where session workspaces live (default `/tmp/tdf_sessions`)
- `SESSION_TTL_MINUTES`: auto-delete sessions idle longer than this (default `60`)
- `MAX_UPLOAD_MB`: per-request upload limit (default `100`)
- `TDF_CORS_ORIGINS`: dev CORS allowlist (default `http://localhost:5173,http://127.0.0.1:5173`)
