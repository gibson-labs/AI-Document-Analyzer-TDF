## TDF Risk Analyzer (React UI + FastAPI wrapper)  

# AI Loan Document Analyzer

An AI-powered document analysis app built to help a loan company review complex borrower and business documents faster, more consistently, and with better visibility into risk.

## Why This Was Built

Loan review can be slow and manual, especially when analysts need to read through financial records, business documents, accident reports, operational data, and supporting files before making a decision. Important details can be buried across multiple PDFs, tables, and reports, which makes the process time-consuming and easy to overlook.

This project was built to solve that problem by using AI to extract, summarize, and analyze key information from uploaded documents. Instead of forcing analysts to manually search through every file, the app helps surface the most relevant details, identify potential risk factors, and generate structured insights that support faster decision-making.

## Business Problem

Loan companies often need to evaluate applications using many different types of documents, including:

- Financial statements
- Credit or underwriting memos
- Safety and accident reports
- Operational reports
- Supporting business documents
- PDFs with tables, images, and unstructured text

Reviewing these manually can create several problems:

- Analysts spend hours reading through documents
- Important risks can be missed
- Different reviewers may evaluate the same file differently
- Document-heavy applications slow down the approval process
- It is difficult to quickly compare strengths, weaknesses, and risk signals

## Solution

This app uses AI-assisted document processing to help analysts move from raw documents to structured insights. It extracts text, analyzes relevant sections, summarizes findings, and helps produce a clearer view of the borrower’s financial and operational risk.

The goal is not to replace human judgment, but to give analysts a faster and more organized way to review large amounts of information before making a decision.


Primary UX: React web app that lets you upload documents, run FedEx/Weighted/Memo analysis, chat with RAG, and download the generated report as Markdown.
 asd
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
