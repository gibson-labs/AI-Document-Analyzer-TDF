# Repository Guidelines
Use this guide when extending the TDF Risk Analysis assistant so every contribution feels consistent, reviewable, and easy to operate.

## Project Structure & Module Organization
- `file.py` orchestrates ingestion, LangChain pipelines, and the weighted analysis CLI; extend or refactor RAG helpers here so the CLI stays the definitive entry point.
- `app.py` hosts the Gradio UI that calls the same helpers; whenever you add a CLI capability, expose matching controls in the UI to keep surfaces aligned.
- `document_loader/` houses sample PDFs, spreadsheets, and JPGs; `chroma/` keeps the persisted vector DB (delete to rebuild, never hand-edit). `.env` stores `OPENAI_API_KEY` and must stay local-only.

## Build, Test, and Development Commands
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python file.py --mode fedex --docs_dir document_loader --persist_dir chroma --company "Acme" --decision_matrix "document_loader/Decision Matrix's.xlsx" --rebuild_index
python file.py --mode weighted --decision_matrix "document_loader/Decision Matrix's.xlsx" --company "Acme"
python file.py --mode memo --company "Acme"
python app.py
```
Set `OPENAI_API_KEY` via `.env` or `export` before any run; use `--mode fedex` for the structured FedEx scoring (default), `--mode weighted` for the legacy matrix-only workflow, and `--mode memo` for the earlier credit memo. Add `--rebuild_index` whenever source docs change to avoid stale embeddings.

## Coding Style & Naming Conventions
- Python 3.10+, 4-space indentation, and snake_case for functions/variables; keep constants like `SUPPORTED_FILE_EXTS` uppercase.
- Type-hint public functions as in `file.py`; include concise docstrings when behavior is not obvious.
- Group imports standard→third-party→local; prefer explicit `from module import symbol` to keep bundle size minimal.
- When adding CLI flags, use descriptive `--long_option` names and mirror defaults between CLI and Gradio.

## Testing Guidelines
- No automated suite exists yet; run the CLI twice (with and without `--rebuild_index`) in both FedEx and weighted modes to verify fresh-build vs cached flows, and save console output for reviewers.
- Exercise each Gradio tab (Summarize, Q&A, FedEx Review, Weighted Analysis) after backend changes and capture representative summaries/scores; when editing decision-matrix code, keep a disposable XLSX in `document_loader/` only for local validation.

## Commit & Pull Request Guidelines
- The repo currently lacks visible Git history, so use an imperative Conventional Commit subject (e.g., `feat: add weighted scoring summaries`) and describe scope + reasoning in the body.
- In PRs, summarize testing (`CLI`, `UI`), call out new flags or env vars, attach screenshots/sample output when the UI or memo format changes, and mention whether embeddings or documents must be regenerated.

## Security & Configuration Tips
- Keep `.env`, API keys, and client documents out of source control; update `.gitignore` before adding new sensitive paths. Remember both the repo-level and per-project `.env` files exist; ensure only the sanitized version is ever referenced in docs.
- Rebuild `chroma/` instead of editing it, and document any Ollama model pulls or other local setup steps inside your PR so reviewers can reproduce the run. When sharing FedEx reviews externally, strip document-level references if they contain private filenames.
