---
date: 2025-12-13T15:53:35-0700
researcher: OpenAI Codex
git_commit: 400fdd3ef9deefd91382ce98c74e584ea7f2fad4
branch: feat-react-frontend-api
repository: Agentic-Systems---Trojan-Debt-Fund
topic: "React frontend + API wrapper for TDF Risk Analysis"
tags: [plan, react, fastapi, ui, rag, chroma]
status: in_progress
last_updated: 2025-12-13
last_updated_by: OpenAI Codex
---

# Implementation Plan: React frontend + API wrapper for TDF Risk Analysis

## Goal
Make a modern React+TypeScript web app the primary way to run this project (upload → analyze → chat → export) while keeping the existing analysis logic in `file.py` unchanged and leaving Gradio (`app.py`) supported as an optional fallback/debug UI (not the main app).

Verification (done when):
- A user can open the React app, upload docs, run FedEx review, chat with RAG, and download the generated report as Markdown via the browser.
- CLI (`python file.py ...`) still works as-is.
- Gradio (`python app.py`) still works as-is.

## Current State
- UI is Gradio and calls analysis helpers in-process (`app.py:1`, `app.py:213`).
- No HTTP API exists; React cannot call the Python functions directly (research doc; also implied by `app.py` direct imports from `file.py:7`).
- Docs are discovered by scanning a folder on disk (`file.py:131`) and indexed into Chroma (`file.py:207`), requiring `OPENAI_API_KEY` (`file.py:234`).
- Key analysis entrypoints already exist and should remain the source of truth:
  - RAG Q&A: `rag_answer` (`file.py:338`)
  - Chat: `chatbot_answer` (`file.py:423`)
  - FedEx review: `fedex_review_analysis` (`file.py:616`)
  - Weighted: `rag_weighted_analysis` (`file.py:742`) with `parse_decision_matrix` (`file.py:650`)
  - CLI orchestration: `main()` (`file.py:864`)

## Proposed Approach
### Decisions (confirmed)
- React is the primary UI; Gradio remains supported but is not the main surface.
- Preserve existing analysis behavior: do not “fix” or restructure weighted/FedEx logic inside `file.py`; wrap it.
- Users upload docs, then explicitly run analysis; decision-matrix weighting is applied automatically on each analysis run (not a separate manual action).
- Chat runs only when the user interacts with the chat UI.
- Keep it demo-simple: no user-facing decision-matrix editing/upload; use a server-side matrix path.
- Prefer ephemeral storage: uploaded docs and indexes do not persist across container restarts, and are cleaned up automatically after inactivity.
- Single deployable container: one container serves both the API and the React UI.

### Repo-vs-guidelines mismatch (called out early)
- AGENTS.md references `document_loader/` as the docs folder, but this repo currently relies on gitignored `files/`, `documents/`, and `chroma/` (`.gitignore`).
- `file.py` defaults to `--docs_dir .../files` and `--decision_matrix .../files/Decision Matrix's.xlsx` (`file.py:875`, `file.py:881`).
Plan follows the codebase as the source of truth while keeping the same conceptual model (docs dir + persist dir + decision matrix).

### Architecture (minimal seams, best practices)
- Add a small Python API wrapper (FastAPI) that:
  - manages per-browser “session” workspaces on disk (ephemeral)
  - accepts uploads into each session
  - auto-builds/opens vectorstores per session (in-memory cache + session-scoped Chroma dirs)
  - calls existing `file.py` helpers to produce outputs (no rewriting prompts/logic)
- Add a React+TypeScript frontend (Vite) that:
  - drives the workflow via the API
  - renders analysis output as a styled report (Markdown → components)
  - provides a polished chat experience (conversation list, streaming-ready UI even if backend is non-streaming initially)

### API boundary (concrete endpoints)
MVP endpoints (optimized for demo simplicity and ephemeral sessions):
- Session
  - `POST /api/session` → create a new session workspace (returns `session_id`)
  - `GET /api/session/{session_id}` → session status (files present, index status, last activity)
  - `DELETE /api/session/{session_id}` → delete session workspace immediately (cleanup disk)
- Documents
  - `POST /api/session/{session_id}/documents` → multipart upload into session docs dir; invalidates index
  - `DELETE /api/session/{session_id}/documents/{filename}` → remove doc; invalidates index
- Analyze (auto-builds index if missing/dirty; matrix auto-applied)
  - `POST /api/session/{session_id}/analyze`
    - default `mode=fedex`
    - advanced `mode=weighted|memo` can exist but may be hidden behind an “Advanced” UI toggle
  - returns `{ output_id, markdown }` and stores the latest markdown in the session
- Chat
  - `POST /api/session/{session_id}/chat` → doc-grounded chat via `chatbot_answer` (`file.py:423`), storing history in the session
- Export
  - `GET /api/session/{session_id}/outputs/{output_id}.md` → download Markdown

Non-goals for MVP (but plan-ready):
- Auth/multi-tenant isolation, S3 storage, and PDF export.

#### Request/response shapes (MVP, explicit + typed)
Use Pydantic models so the frontend can be strictly typed.
- `POST /api/session`
  - req: `{ "company": "Acme" }` (optional; for display only)
  - resp: `{ "session_id": "s_...", "company": "Acme", "created_at": "..." }`
- `POST /api/session/{session_id}/documents`
  - multipart: `files[]`
  - resp: `{ "files": [{ "name": "...", "bytes": 123, "ext": ".pdf" }], "invalidated": true }`
- `POST /api/session/{session_id}/analyze`
  - req:
    - `mode`: `"fedex" | "weighted" | "memo"` (default `"fedex"`)
    - `summarizer_spec`: optional string (maps to `make_llm_from_spec`)
    - `analyzer_spec`: optional string
  - resp: `{ "output_id": "...", "markdown": "..." }`
- `POST /api/session/{session_id}/chat`
  - req: `{ "message": "...", "summarizer_spec": "...?", "analyzer_spec": "...?" }`
  - resp: `{ "assistant": "..." }`

### Data model & filesystem layout
Create a `sessions/` folder inside the container filesystem (default under `/tmp`) so it is naturally ephemeral:
- `/tmp/tdf_sessions/{session_id}/session.json` (company name, timestamps, last_activity)
- `/tmp/tdf_sessions/{session_id}/docs/` uploaded originals
- `/tmp/tdf_sessions/{session_id}/chroma/` persisted vector DB for the life of the session
- `/tmp/tdf_sessions/{session_id}/outputs/{output_id}.md` generated report(s) for export
- `/tmp/tdf_sessions/{session_id}/conversations/{conversation_id}.json` chat history

Cleanup model:
- On normal page close: frontend sends `DELETE /api/session/{session_id}` using `navigator.sendBeacon` (best-effort).
- Server also runs a TTL cleanup loop (e.g., delete sessions idle for 30–60 minutes) to avoid disk growth even if clients disconnect uncleanly.

### Caching strategy (fast + correct)
- In-memory cache keyed by `session_id` for:
  - opened `vectordb`
  - pre-summary (optional)
- Invalidate cache when:
  - documents uploaded
  - documents deleted
- Prefer “open existing Chroma” when session `chroma/` exists; otherwise build via `build_vectorstore` (`file.py:207`).

### Frontend UX (beautiful + modern)
Primary flow:
- Analyzer: start a session → upload docs → run analysis (FedEx default) → view report → export.
- Chat: ask questions only when the user uses Chat (doc-grounded, citations styled).

Concrete UX requirements (implementation-ready):
- Analyzer page:
  - lightweight session header (“New session” resets everything)
  - upload dropzone with progress + file list + remove action
  - analysis mode selector (FedEx default; advanced toggle reveals Memo/Weighted)
  - “Run analysis” button with clear state machine: idle → running → success/error
  - report viewer:
    - render Markdown into a styled “report” layout
    - sticky TOC generated from headings
    - highlight `Source: filename (page N)` blocks (pattern emitted in `file.py:466`)
    - export button downloads `.md` via API
- Chat page:
  - single conversation MVP (no conversation list)
  - transcript (citations visually distinct; copy message)
  - composer (enter to send; shift+enter newline)
  - empty states that guide: “Upload docs first”, “Run analysis first” (optional)

Design best practices:
- Accessible components (keyboard nav + focus visible), responsive layout, consistent typography/spacing.
- Polished loading: skeletons for report, inline upload progress, non-blocking toasts for background events.

### Tech choices (recommended defaults)
- Frontend: Vite + React + TypeScript, Tailwind CSS, `react-markdown` (+ `remark-gfm`), TanStack Query (server state), TanStack Router or React Router, Zod for request/response validation.
- UI primitives: shadcn/ui (Radix) or equivalent accessible component kit for consistent styling.
- Backend: FastAPI + Uvicorn, Pydantic models, `python-multipart` for uploads; CORS locked to localhost dev origin.

### Backend design details (implementation notes)
#### Configuration (env vars; defaults safe for local)
- `TDF_SESSIONS_ROOT` (default: `/tmp/tdf_sessions`) — where session workspaces live (ephemeral).
- `TDF_DECISION_MATRIX_PATH` (default: match `file.py` default decision matrix path) — the “default” matrix used for analysis runs.
- `MAX_UPLOAD_MB` (default: 100) — server-side size limit (FastAPI doesn’t enforce automatically; implement checks).
- `SESSION_TTL_MINUTES` (default: 60) — delete sessions idle longer than this.

#### Session ID strategy
- Generate `session_id` as a short, URL-safe id (e.g., `s_` + base32/uuid).
- Store a human-readable `company` in metadata for display only; no long-lived “case management” in MVP.

#### File validation and security rules
- Extension allowlist must match `SUPPORTED_FILE_EXTS` in `file.py:23` (`.pdf`, `.jpg`, `.jpeg`, `.png`, `.xlsx`, `.xls`).
- Sanitize filenames:
  - drop path separators, control characters
  - de-duplicate by appending ` (1)`, ` (2)` if names collide
- Never accept arbitrary server filesystem paths from the browser.
- Ensure all filesystem operations resolve within the computed session root (defense-in-depth against path traversal).
 - Ensure all filesystem operations resolve within the computed session root (defense-in-depth against path traversal).

#### Vectorstore lifecycle (core correctness)
- “Ready” means: Chroma directory exists for the session AND can be opened OR embeddings can be rebuilt.
- Implement per-session locks for:
  - index build/rebuild
  - analyze (analysis assumes a stable retriever)
  - optional: chat (simplest: share the same lock as analyze)
- Cache in memory:
  - `vectordb` (opened Chroma)
  - optional `pre_summary` keyed by (case_id, summarizer_spec)
- Invalidation:
  - any doc add/remove marks `index_state = dirty` and clears in-memory `vectordb` for that session.
  - rebuild resets `dirty=false` and updates `built_at`.

#### Decision matrix strategy (applies every analysis run)
Default behavior:
- On each `/analyze` request, resolve the decision matrix file using the global default only (`TDF_DECISION_MATRIX_PATH` or `file.py` default path).
- Parse it fresh each run with `parse_decision_matrix` (`file.py:650`) to avoid stale weights if the matrix is tuned.

Mode-specific behavior:
- `fedex`:
  - If matrix loads successfully: pass `criteria_weights` into `fedex_review_analysis` (`file.py:616`).
  - If matrix missing/unreadable: run FedEx review anyway with `criteria_weights=None` (keep UX resilient).
- `weighted`:
  - If matrix loads successfully: run `rag_weighted_analysis` (`file.py:742`).
  - If matrix missing/unreadable: return a 400 with a clear message (“Decision matrix missing; upload one or configure default.”) because the mode is matrix-driven.
- `memo`:
  - Ignore matrix entirely.

#### Error mapping (backend → frontend)
Map common exceptions into a small set of API error codes:
- `OPENAI_API_KEY_MISSING` (from `file.py:234` RuntimeError)
- `NO_DOCUMENTS` (from `discover_documents` / empty session docs)
- `INDEX_DIRTY` (docs changed; requires rebuild or lazy rebuild)
- `OPENAI_QUOTA` / `OPENAI_RATE_LIMIT`
- `UNSUPPORTED_FILE_TYPE`
- `UPLOAD_TOO_LARGE`
- `INTERNAL_ERROR` (fallback)

Return shape example:
`{ "error": { "code": "NO_DOCUMENTS", "message": "...", "hint": "Upload PDFs/XLSX then retry." } }`

### Frontend design details (implementation notes)
#### State model
- Store `session_id` in memory (and optionally `sessionStorage`) so refresh is stable during a session.
- Server state via TanStack Query:
  - session status
  - uploaded file list
  - conversations list (optional; can be “single conversation” for MVP)
- UI-only state:
  - uploader local queue + progress
  - analysis config (mode + model specs)
  - chat composer value

#### Report rendering
- Render Markdown to semantic HTML with a consistent “report” theme:
  - headings mapped to typographic scale
  - blockquotes/callouts for “Evidence-Backed Justifications”
  - code fences styled (for JSON blocks if any)
- Add a citation highlighter:
  - detect `Source:` lines and wrap them in a distinct component
  - optionally link sources to a “Documents” panel (filename matching)

#### “Beautiful modern” baseline checklist
- Layout: sidebar + topbar + content; responsive collapse for narrow widths.
- Visual: neutral background, elevated cards, consistent radii, subtle shadows.
- Interaction: toasts for success/error, skeleton loading, optimistic UI for rename/delete.
- Accessibility: labeled inputs, focus management, aria for dialogs, keyboard nav for menus.

## Plan (Phases)
### Progress (update as work completes)
- [x] Phase 1: Confirm defaults
- [x] Phase 2: Backend skeleton
- [x] Phase 3: Session storage
- [x] Phase 4: Upload flow
- [x] Phase 5: Index + analysis runner
- [x] Phase 6: Chat endpoint
- [x] Phase 7: Export endpoint
- [x] Phase 8: Frontend skeleton
- [x] Phase 9: Analyzer UI end-to-end
- [x] Phase 10: Chat UI end-to-end
- [x] Phase 11: UX polish + hardening
- [x] Phase 12: Containerization (single container)
- [x] Phase 13: Docs + runbook
- [ ] Phase 14: Manual verification

1. Confirm defaults (ports, TTL, matrix path)
2. Backend skeleton (FastAPI app, CORS, health, typed models)
3. Session storage (ephemeral layout, metadata, safe filenames, TTL cleanup)
4. Upload flow (validation, progress, invalidation semantics)
5. Index + analysis runner (auto-build index on analyze; matrix auto-applied)
6. Chat endpoint (doc-grounded; session history persisted)
7. Export endpoint (download markdown output)
8. Frontend skeleton (Vite TS, Tailwind, app shell)
9. Analyzer UI end-to-end (start session, upload, run, report render, export)
10. Chat UI end-to-end (single conversation MVP; transcript + composer)
11. UX polish + hardening (errors, empty states, limits, a11y)
12. Containerization (single Docker image serving UI+API)
13. Docs + runbook (README: docker run is primary; Gradio optional)
14. Manual verification (CLI + UI)

Pause point: after Phase 5 (analyze returns markdown), validate end-to-end before UI polish.

### Phase detail checklists (what to implement per phase)
1) Confirm defaults
- Decide dev ports: API `http://localhost:8000`, frontend `http://localhost:5173`.
- Decide primary run path for users:
  - dev: run API + frontend separately
  - prod-like: build frontend and have FastAPI serve static files

Confirmed defaults (implemented as the target configuration):
- Dev API port: `8000` (Uvicorn)
- Dev frontend port: `5173` (Vite)
- Container port: `8080` (single container serves UI + API under `/api/*`)
- Session TTL: `60` minutes (`SESSION_TTL_MINUTES=60`)
- Session root: `/tmp/tdf_sessions` (ephemeral; `TDF_SESSIONS_ROOT=/tmp/tdf_sessions`)
- Default decision matrix path: `files/Decision Matrix's.xlsx` (override via `TDF_DECISION_MATRIX_PATH`)

2) Backend skeleton
- Add `server.py` with FastAPI app factory, `/api/health`, and version info.
- Add CORS middleware for dev origin(s) only.
- Add Pydantic request/response models and a unified error response.

3) Session storage
- Implement `storage/sessions.py`:
  - create/get/delete session
  - safe path helpers for docs/chroma/outputs/conversations
  - metadata read/write (`session.json`, `last_activity`)
  - TTL cleanup loop (background task)

4) Document upload
- Implement upload endpoint:
  - stream upload to disk
  - validate extension + size
  - sanitize filenames and handle collisions
  - mark session “dirty” and delete in-memory cache for that session
- Implement delete document endpoint with same invalidation.

5) Index management
- Implement:
  - “open existing Chroma” path if present
  - rebuild/build logic that clears session chroma folder and calls `load_docs` + `build_vectorstore`
  - `index/status` endpoint computed from metadata + filesystem
- Add per-session `asyncio.Lock` (or file-based lock) for rebuild/analyze.

6) Analysis runner
- Implement `/analyze` with:
  - lazy index: if index is missing or dirty, auto-build before running (demo UX).
  - decision matrix resolution + parsing on each run
  - call into `file.py` functions:
    - `fedex_review_analysis` for FedEx
    - `rag_weighted_analysis` for Weighted
    - `rag_answer` for Memo
  - persist Markdown output and return it.

7) Conversations + chat
- MVP: single conversation stored in session; optionally add create/list/delete later.
- Implement `/chat`:
  - loads conversation history
  - ensures index exists (or returns a helpful error)
  - calls `chatbot_answer` and persists updated history

8) Export endpoints
- Add `GET` for output Markdown (download by `output_id`).

9) Frontend skeleton
- Scaffold `frontend/` with Vite React TS.
- Add Tailwind + component library (shadcn/ui recommended).
- Add routing structure and global layout.
- Add API client module + Zod schemas for responses.

10) Analyzer UI
- “Start session” button on load (or auto-create session on first upload).
- Upload wired to `/documents` with progress.
- Run analysis wired to `/analyze` and renders Markdown with a report theme.
- Export button uses `/outputs/{id}.md`.

11) Chat UI
- Chat transcript and composer wired to `/chat` (single conversation MVP).
- Render citations clearly; show “upload docs first” empty states.

11) Polish + hardening
- Tighten error handling and map backend error codes to friendly UX.
- Add debounce for chat submit; prevent double submits.
- Add confirmation dialogs for destructive actions (delete session/docs).
- Ensure keyboard nav + focus trap in dialogs.

12) Containerization (single container)
- Add a multi-stage `Dockerfile`:
  - Stage 1 (node): install frontend deps and build `frontend/dist`
  - Stage 2 (python): install `requirements.txt`, copy backend + `frontend/dist`
  - Serve static UI from FastAPI (e.g., mount `/` to `frontend/dist`) and keep API under `/api/*`
  - Run with `uvicorn server:app --host 0.0.0.0 --port 8080`
- Keep session storage under `/tmp/tdf_sessions` in the container (ephemeral by default).
- Document how to pass `OPENAI_API_KEY` into the container.

13) Docs + runbook
- Update `README.md` with:
  - primary run steps for Docker
  - local dev run steps for React+API
  - “Gradio optional” section
  - troubleshooting for missing `OPENAI_API_KEY`, OCR deps, quota

14) Manual verification
- Follow the AGENTS.md manual steps for CLI (FedEx/weighted; rebuild/cached) and a UI pass for Analyzer + Chat.

## Files Touched
- `requirements.txt` — add FastAPI server deps (FastAPI, uvicorn, python-multipart)
- `server.py` (new) — FastAPI app + endpoints + session storage
- `storage/` (new) — session filesystem helpers (paths, metadata, TTL cleanup, safe filenames)
- `services/` (new) — per-session vectordb cache + decision matrix loader + analysis/chat runners (thin wrappers)
- `app.py` — keep supported; optionally add a small UI banner pointing to React (no behavior change)
- `README.md` — make Docker run the primary run instructions; keep Gradio/CLI documented
- `thoughts/shared/research/2025-12-13-react-frontend-plan.md` — no changes (source research)
- `frontend/` (new) — React app (Vite) with components, styles, API client
- `Dockerfile` (new) — single container builds UI and runs FastAPI
- `.dockerignore` (new) — keep builds small/fast

## Testing & Verification
### Automated
- [x] `python3 -m py_compile server.py file.py app.py storage/*.py services/*.py`
- [ ] Minimal smoke via curl (no network install assumptions):
  - [ ] create session, upload file, analyze, chat, download output
### Manual
- [ ] CLI: run twice (with/without `--rebuild_index`) in `--mode fedex` and `--mode weighted` (per AGENTS.md)
- [ ] UI:
  - [ ] start session, upload docs, run FedEx review, verify report renders
  - [ ] chat about uploaded docs, verify citations render
  - [ ] export Markdown downloads
  - [ ] start a new session and repeat upload/analyze quickly

## Risks / Rollout
- Embeddings require `OPENAI_API_KEY` and network access; surface clear UI errors using existing exception text (`file.py:234`).
- Index builds can be slow; show progress states and avoid blocking the UI thread (frontend spinners, backend background task optional).
- Disk growth from uploads/chroma; enforce TTL cleanup and a “new session” reset action.
- Concurrency: multiple requests per session can race; guard with per-session locks for build/analyze.
- Security: restrict operations to session root; sanitize filenames; limit upload size; keep decision matrix path server-side only.

## Out of Scope
- Authentication/authorization, multi-user tenancy, and cloud deployment.
- Streaming token-by-token chat (design UI ready, but backend can return full responses first).
- S3-backed storage and PDF export (plan keeps seams to add later).
