---
date: 2025-12-13
branch: main
repo: Agentic-Systems---Trojan-Debt-Fund
scope: research-only (no behavior changes)
---

## Summary
The current “frontend” is Gradio (`app.py`) calling helper functions in `file.py`. There is no HTTP API layer, so a React+TypeScript UI will require adding a thin backend (API boundary) that invokes the *existing* analysis + agent code without modifying it. For a single-user demo with local file storage, the cleanest seam is: **(React UI) → (API wrapper) → (existing functions in `file.py` and agents)**.

Your constraints:
- Keep AWS/Textract ingestion code and chatbot logic intact; only change how the UI accesses/displays them.
- Reduce UI surface (not 5 Gradio tabs); prefer a simpler workflow: upload → analyze → chat → export.
- Local storage for docs now; note future S3 option.

## Repo Snapshot (Entry Points + Responsibilities)
- `file.py` is the main orchestration layer and CLI entrypoint (`file.py:864`).
  - Ingestion + indexing: `discover_documents` (`file.py:122`), `load_docs` (`file.py:108`), `build_vectorstore` (`file.py:207`)
  - “Memo/Q&A”: `rag_answer` (`file.py:338`)
  - “Chatbot”: `chatbot_answer` (`file.py:423`) and `general_chat_answer` (`file.py:360`)
  - “FedEx Review”: `fedex_review_analysis` (`file.py:616`) with section context retrieval (`file.py:68`) + formatted output (`file.py:559`)
  - “Weighted Analysis”: decision matrix parsing `parse_decision_matrix` (`file.py:650`) + `rag_weighted_analysis` (`file.py:761`)
- `app.py` is the current Gradio UI (`app.py:213`) that calls `file.py` helpers directly (`app.py:7`).
  - Tabs: Summarize (`app.py:239`), Q&A (`app.py:244`), Chatbot (`app.py:250`), FedEx Review (`app.py:282`), Weighted Analysis (`app.py:288`)
- AWS/Textract / agents (currently not wired into `app.py`):
  - Text extraction w/ Textract fallback: `text_extraction.py` (`text_extraction.py:33`+)
  - Table/Textract + SQLite-building agent tooling: `table_agent.py` (`table_agent.py:103`+)
  - Vision extraction agent writing to `extracted_text/`: `vision_extraction_agent.py` (`vision_extraction_agent.py:21`, `vision_extraction_agent.py:54`)

## Concrete Findings Relevant to a New React UI
### 1) There is no server/API today
- Gradio calls Python functions in-process (`app.py:242`, `app.py:248`, `app.py:275`, etc.).
- A React UI cannot call those directly; it needs HTTP endpoints.

**Seam for change:** add a new API layer file (e.g., `server.py`) that imports and calls existing functions.

### 2) Document input expectations are “folder-based”
- Both CLI and Gradio rely on a docs directory that is scanned for supported file extensions (`SUPPORTED_FILE_EXTS` in `file.py:23`; used by `discover_documents` in `file.py:122`).
- Browsers can’t safely “hand you a folder” in a portable way; typical pattern is **upload files → store server-side → run analysis on that folder**.

**Seam for change:** define a “case” directory on disk per run/user.

### 3) Indexing/Chroma persistence is present but currently built lazily in Gradio
- Gradio caches `_state["vectordb"]` and builds it on first request (`app.py:23`–`app.py:38`).
- CLI supports `--rebuild_index` by clearing files in the persist dir (`file.py:899`–`file.py:905`).

**Seam for change:** API needs endpoints to:
- upload docs (invalidates cache)
- rebuild index (optional)
- reuse existing persisted index when possible

### 4) Chatbot behavior is encapsulated in `file.py` (keep intact)
- Doc-grounded chatbot: `chatbot_answer` builds conversation history + retrieves context via retriever (`file.py:447`–`file.py:482`).
- General chat fallback exists (`general_chat_answer`, `file.py:360`).
- Current Gradio “Chatbot” tab builds message history in a specific format (`app.py:82`–`app.py:210`).

**Seam for change:** API should store chat history per “conversation id” and pass it into `chatbot_answer` unchanged.

### 5) “Export analysis” does not exist yet
- Outputs are currently returned as strings printed to terminal or displayed in Gradio textboxes.

**Seam for change:** API should provide “download/export” endpoints. MVP can export Markdown/text, with optional PDF generation later.

## Recommended Minimal UX (Replaces 5 Tabs)
**Goal:** single workflow with two primary experiences:
1) **Analyze**: upload docs, set company, choose analysis type (FedEx Review vs Weighted vs Memo), run, view output, export.
2) **Chat**: ask follow-ups about the same docs (doc-grounded chat), keep conversation history, show citations.

### Proposed UI Screens
- **Case / Upload**
  - Company name
  - Upload documents (PDF, images, XLSX)
  - “Build/Rebuild Index” (optional, with status)
- **Analyze**
  - Select analysis mode: `fedex` / `weighted` / `memo`
  - If `weighted`, select decision matrix file among uploaded docs (or upload separately)
  - Run analysis → render output (Markdown viewer)
  - Export: download `.md` (MVP), optional “Export PDF”
- **Chat**
  - Chat panel (messages)
  - Uses the same case docs/index
  - “New conversation” / “Clear”

## Implementation Outline (mapping change onto codebase)
### Option A (recommended): Add a small FastAPI backend + React frontend
Why: clean separation, supports Docker, minimal changes to existing analysis code.

Touched / new files (high level):
- New: `server.py` (or `backend/app.py`) exposing HTTP endpoints.
- New: `frontend/` React+TS app (Vite).
- New: `Dockerfile` + `docker-compose.yml` for single-user demo deployment.
- Update: `README.md` with run commands and “future S3” note.

Endpoints should be thin wrappers around existing functions:
- `POST /cases` → create case directory + metadata
- `POST /cases/{id}/documents` → upload docs to that case directory
- `POST /cases/{id}/index/rebuild` → clear and rebuild persisted chroma
- `POST /cases/{id}/analyze` → call one of:
  - `fedex_review_analysis` (`file.py:616`)
  - `rag_weighted_analysis` (`file.py:761`)
  - `rag_answer` memo query (`file.py:338`)
- `POST /cases/{id}/chat` → call `chatbot_answer` (`file.py:423`) (fallback to `general_chat_answer`, `file.py:360`) and persist history
- `GET /cases/{id}/outputs/{name}` → download `.md` (MVP)
- `POST /cases/{id}/outputs/{name}/pdf` → optional PDF export (later; keep as “nice to have”)

### Option B: Keep Gradio, embed React later
Why: least change, but doesn’t meet “official frontend” goal and limits UX control.

Recommendation: **Option A**.

## Edge Paths / Risks (important for the demo)
- Missing docs directory or empty folder: `discover_documents` errors (`app.py:33`–`app.py:35`, `file.py:896`–`file.py:897`).
- Missing OpenAI key: `build_vectorstore` hard-fails (`file.py:234`–`file.py:239`) and CLI hard-fails (`file.py:888`–`file.py:891`).
- Weighted analysis without decision matrix: should be a clear UI validation error (since `parse_decision_matrix` expects a file, `file.py:653`–`file.py:655`).
- Repo mismatch: current Gradio defaults to `files/` (`app.py:222`) but that directory does not exist in the workspace; React flow should avoid “folder path textboxes” entirely.

## “Future S3” Note (where to document)
- Add a short section in `README.md` explaining that documents are currently stored in per-case local folders, with a future option to swap storage to S3 (upload endpoint writes to disk today; later could stream to S3 and download to temp for processing).

## Verification Checklist (manual, since no tests)
- Create a case, upload sample docs, build index, run FedEx review and memo.
- Run chat with follow-up questions; verify citations appear.
- Export Markdown; if PDF export exists, verify file downloads and opens.

---

## Detailed Model of Current Behavior (what must not change)

### Ingestion + indexing (vector DB)
- Supported file types are hard-coded as `SUPPORTED_FILE_EXTS` (`file.py:23`) and used by `discover_documents` (`file.py:131`–`file.py:140`).
- `load_docs` does multi-loader extraction and attaches source metadata (`file.py:143`–`file.py:204`):
  - PDFs: tries `PyPDFLoader` first and falls back to `UnstructuredFileLoader(..., strategy="hi_res")` for OCR if no text (`file.py:149`–`file.py:169`).
  - Images: `UnstructuredImageLoader` with special handling for missing Tesseract (`file.py:180`–`file.py:199`).
  - Excel: `UnstructuredExcelLoader` (`file.py:190`–`file.py:192`).
  - Metadata: ensures `metadata["source"]` is set (used for citations) (`file.py:200`–`file.py:203`).
- `build_vectorstore` enforces content and the presence of `OPENAI_API_KEY` (`file.py:207`–`file.py:239`). This is a key invariant: **index build requires OpenAI embeddings**.

Implication for new UI:
- Backend/API must validate “docs present” and “OPENAI_API_KEY set” early and surface friendly errors (instead of stack traces).
- For a demo, keep analysis deterministic/traceable: preserve citations by not stripping `metadata["source"]` logic.

### Analysis modes that exist today
- “Memo” / single-turn analysis is `rag_answer` which retrieves `k=8` MMR snippets and asks for a memo with citations (`file.py:338`–`file.py:357`).
- “Chatbot” is `chatbot_answer`, which:
  - builds a history string from the last 5 exchanges (`file.py:447`–`file.py:456`)
  - retrieves `k=8` MMR snippets (`file.py:458`–`file.py:472`)
  - uses `make_chatbot_prompt()` and returns the LLM response (`file.py:475`–`file.py:482`)
- “FedEx Review” is `fedex_review_analysis` which:
  - retrieves section-specific context using pre-defined queries (`collect_section_context`, `file.py:68`–`file.py:84`)
  - enforces a JSON schema in the prompt (`file.py:87`–`file.py:128`)
  - formats into a human-readable report (`format_fedex_review_output`, `file.py:559`–`file.py:613`)
  - computes score and risk band fallback (`file.py:641`–`file.py:647`)
- “Weighted Analysis” is `rag_weighted_analysis`, which:
  - parses decision weights using `parse_decision_matrix` (`file.py:650`–`file.py:723`)
  - retrieves `k=12` snippets (`file.py:742`–`file.py:756`)
  - asks model to return JSON and then computes its own weighted score + recommendation (`file.py:758`+)

Implication for new UI:
- You can keep the analysis engine unchanged, but the UI should expose only the modes you want (e.g., “FedEx Review” and “Memo”) and treat “Weighted” as optional/advanced.
- “Decision matrix parsing” prints a lot of debugging output (`file.py:658` and `file.py:707`). If the demo is public-facing, consider redirecting stdout in the wrapper layer (without altering core code).

### Current Gradio UI behavior (baseline)
- Settings (docs folder, persist folder, company, LLM specs) are just textboxes (`app.py:232`–`app.py:237`).
- Chat is stored client-side in the Gradio session and re-parsed each request (`app.py:130`–`app.py:173`).
- The app launches with `share=True` (`app.py:300`), which relies on outbound networking and will be unreliable/blocked in some environments.

Implication for new UI:
- Avoid “folder path” textboxes; use “upload files” + store on server.
- Persist chat server-side keyed by case + conversation id so refresh doesn’t lose history.

---

## Backend/API Seam Design (detailed)

### Why an API layer is required
- React runs in the browser and cannot call Python functions in-process like Gradio does.
- The API layer must be a **thin adapter** that:
  - writes uploads to disk
  - calls existing functions (`file.py`, optional `text_extraction.py` / `table_agent.py` / `vision_extraction_agent.py`)
  - returns strings/JSON back to the UI

### Dependency note (important for planning)
- The current `requirements.txt` does **not** include a web framework like FastAPI/Flask (no `fastapi` matches). A plan must include adding backend deps (and pinning them) if you choose FastAPI.

### “Case” concept (recommended for a single-user demo)
Because “docs_dir” is folder-based (`discover_documents`, `file.py:131`), the wrapper should create a per-run folder layout:
- `cases/{case_id}/docs/` → uploaded PDFs/images/XLSX
- `cases/{case_id}/chroma/` → persisted Chroma DB
- `cases/{case_id}/outputs/` → saved analysis outputs (Markdown/text) + exported PDFs
- `cases/{case_id}/conversations/` → chat histories (JSON)
- Optional:
  - `cases/{case_id}/extracted_text/` → outputs from `text_extraction.py` and `vision_extraction_agent.py` (these currently default to `extracted_text/` at repo root: `text_extraction.py:31`, `vision_extraction_agent.py:22`)
  - `cases/{case_id}/extracted_data.db` → SQLite output from `table_agent.py` (`table_agent.py:25`)

### Minimal endpoint set (maps to your simplified UX)
1) **Case**
   - `POST /cases` create case metadata (`company`, maybe `created_at`)
   - `GET /cases` list cases (for convenience)
2) **Documents**
   - `POST /cases/{id}/documents` upload one or more files into `docs/`
   - `GET /cases/{id}` return metadata + file list
3) **Index**
   - `POST /cases/{id}/index/rebuild` explicit rebuild (mirrors `--rebuild_index`, `file.py:899`–`file.py:905`)
   - (optional) lazy index build on first analyze/chat
4) **Analyze**
   - `POST /cases/{id}/analyze` with:
     - `mode`: `fedex` (primary) or `memo` (secondary) or `weighted` (optional)
     - `summarizer` / `analyzer` LLM specs (match existing `make_llm_from_spec`, `file.py:319`)
     - optional decision matrix filename (for weighted/fedex criteria references)
   - Returns: analysis string + `output_id` for exporting/downloading
5) **Chat**
   - `POST /cases/{id}/chat` with:
     - `conversation_id`
     - `message`
     - optional `summarizer` for pre-summary
     - `analyzer` LLM spec
   - Uses `chatbot_answer` (`file.py:423`) and stores history server-side.
6) **Export**
   - `GET /cases/{id}/outputs/{output_id}.md` download
   - Optional: `POST /cases/{id}/outputs/{output_id}/pdf`

### Caching strategy (what matters for UX)
- `build_vectorstore` is expensive (embeddings). The wrapper should:
  - keep an in-memory cache keyed by `case_id`
  - invalidate cache on upload/rebuild
  - optionally attempt to “re-open” an existing Chroma DB from `persist_dir` if it exists (to avoid re-embedding)
  - preserve the current behavior that embeddings require `OPENAI_API_KEY` (`file.py:234`–`file.py:239`)

---

## Frontend UX Spec (detailed, aligned to your “simple” requirement)

### Primary navigation (2 main sections)
1) **Analyzer**
   - Step 1: Create/select “case” (company name)
   - Step 2: Upload documents (drag/drop)
   - Step 3: Choose analysis type:
     - Default: **FedEx Review**
     - Optional advanced: Memo / Weighted
   - Step 4: Run analysis
   - Step 5: Export (download Markdown; optional “Export PDF”)
2) **Chat**
   - Chat about the uploaded docs for the selected case
   - Conversation picker (“New chat”, “Rename”, “Delete”) for demo polish

### How to display outputs for a non-technical user
- Prefer a “report layout” rather than a raw textbox:
  - Render Markdown with headings and section cards
  - Add a “Sources” drawer that highlights `Source: filename (page N)` patterns (produced by `rag_answer` and `chatbot_answer`, `file.py:345`–`file.py:348`, `file.py:466`–`file.py:470`)
- Export:
  - MVP: download Markdown/text (fast and robust)
  - PDF: optional; if implemented, do it in the API layer so the browser just downloads a file

---

## AWS/Textract + Agents (how to “access, not modify” them)

### Text extraction
- `text_extraction.py` is a file-driven extractor with Textract + fallbacks and writes `.txt` files (`text_extraction.py:145`–`text_extraction.py:167`) to `DEFAULT_OUTPUT_DIR` (`text_extraction.py:31`).
Integration approach (no behavior change):
- API wrapper can invoke its `main()` / functions on a per-case docs folder, and store outputs under the case.

### Table extraction / SQLite agent
- `table_agent.py` uses AWS Textract for tables and writes/query SQLite (`table_agent.py:103`+, `table_agent.py:186`+).
Integration approach (no behavior change):
- Provide an “Extract Tables” optional action in the UI that runs this and surfaces a “download DB” button, but keep it out of the default path if you want simplicity.

### Vision extraction agent
- `vision_extraction_agent.py` writes grouped summaries to `extracted_text/` (`vision_extraction_agent.py:22`, `vision_extraction_agent.py:63`).
Integration approach (no behavior change):
- Add an “Extract visuals” action that runs the agent and shows the produced `*-vision.txt` files; optionally feed those into the docs folder for indexing later (but that would be a behavior decision for the wrapper layer).

---

## Docker/Home-Server Deployment Notes (for later planning)
- Because this is single-user, simplest deployment is a single container (or compose) that runs:
  - backend API server
  - serves built frontend static files
  - mounts a host volume for `cases/` so uploaded docs and chroma persist across restarts
- Environment variables:
  - `OPENAI_API_KEY` must be present at runtime (hard requirement for index build; `file.py:234`–`file.py:239`)
  - AWS creds are used by Textract-related modules (`text_extraction.py:25`–`text_extraction.py:27`, `table_agent.py:22`–`table_agent.py:25`)

---

## Future S3 Storage Option (concrete plan hook)
To enable “local now, S3 later” without rewriting everything:
- Introduce a storage interface in the wrapper layer only:
  - `put_object(case_id, filename, bytes)` and `list_objects(case_id)` and `get_local_path(case_id, filename)`
- Implement `LocalCaseStorage` first (writes to `cases/{id}/docs/`).
- Later add `S3CaseStorage` (writes to S3, downloads to temp for `discover_documents`/`load_docs` which expect filesystem paths).
This keeps core analysis unchanged while making storage swappable.
