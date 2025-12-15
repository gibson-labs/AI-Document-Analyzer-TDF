import asyncio
import os
import platform
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any, Optional
from dotenv import load_dotenv

from fastapi import FastAPI, Request
from fastapi import File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from pathlib import Path

load_dotenv()

from services.analysis import run_analysis
from services.chat import run_chat
from services.indexing import ensure_vectorstore
from services.state import clear_session_cache, get_session_state
from storage.sessions import (
    SessionPaths,
    create_session,
    delete_session,
    dedupe_filename,
    get_session_paths,
    is_supported_filename,
    read_session_metadata,
    sanitize_filename,
    set_index_dirty,
    touch_session,
    ttl_cleanup_loop,
)


APP_VERSION = "0.1.0"


class ErrorDetail(BaseModel):
    code: str = Field(..., examples=["INTERNAL_ERROR"])
    message: str
    hint: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    version: str
    utc_time: str
    python: str
    platform: str


class CreateSessionRequest(BaseModel):
    company: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str


class DocumentInfo(BaseModel):
    name: str
    size_bytes: int
    modified_utc: str


class SessionStatusResponse(BaseModel):
    session_id: str
    company: Optional[str] = None
    created_utc: Optional[str] = None
    last_activity_utc: Optional[str] = None
    index_dirty: bool = True
    documents: list[DocumentInfo] = Field(default_factory=list)


class IndexStatusResponse(BaseModel):
    status: str = Field(..., examples=["missing", "dirty", "ready"])
    index_dirty: bool
    document_count: int


class AnalyzeRequest(BaseModel):
    company: str = Field(..., examples=["Acme"])
    mode: str = Field(default="fedex", examples=["fedex", "weighted", "memo"])
    summarizer_spec: Optional[str] = Field(default=None, examples=["openai:gpt-4o-mini"])
    analyzer_spec: Optional[str] = Field(default=None, examples=["openai:gpt-4o-mini"])


class AnalyzeResponse(BaseModel):
    output_id: str
    markdown: str


class ChatRequest(BaseModel):
    company: str = Field(..., examples=["Acme"])
    message: str
    conversation_id: str = Field(default="default")
    summarizer_spec: Optional[str] = Field(default=None, examples=["openai:gpt-4o-mini"])
    analyzer_spec: Optional[str] = Field(default=None, examples=["openai:gpt-4o-mini"])


class ChatResponse(BaseModel):
    message: str
    response: str
    conversation_id: str


def _parse_cors_origins(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = asyncio.create_task(ttl_cleanup_loop(stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(Exception):
                await task

    app = FastAPI(title="TDF Risk Analyzer API", version=APP_VERSION, lifespan=lifespan)

    cors_origins = _parse_cors_origins(
        os.getenv(
            "TDF_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message="Unexpected server error.",
                    hint="Check server logs for details.",
                )
            ).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = str(getattr(exc, "detail", "") or "")
        status = int(getattr(exc, "status_code", 500) or 500)
        code = "HTTP_ERROR"
        hint: Optional[str] = None

        if status == 404 and detail == "Session not found.":
            code = "SESSION_NOT_FOUND"
            hint = "Create a new session and retry."
        elif status == 400 and detail == "Invalid session_id.":
            code = "INVALID_SESSION_ID"
            hint = "Create a new session and retry."
        elif status == 404 and detail == "Document not found.":
            code = "DOCUMENT_NOT_FOUND"
        elif status == 404 and detail == "Output not found.":
            code = "OUTPUT_NOT_FOUND"
        elif status == 400 and detail.startswith("Unsupported file type:"):
            code = "UNSUPPORTED_FILE_TYPE"
            hint = "Upload .pdf, .jpg/.png, or .xlsx/.xls files."
        elif status == 413 and detail == "Upload too large.":
            code = "UPLOAD_TOO_LARGE"
            hint = "Reduce file size or lower the number of files per upload."
        elif status == 400 and detail == "No documents uploaded.":
            code = "NO_DOCUMENTS"
            hint = "Upload PDFs/XLSX then retry."
        elif status == 400 and detail == "No extractable text found in uploaded documents.":
            code = "NO_EXTRACTABLE_TEXT"
            hint = "If you uploaded images, install OCR (Tesseract) or upload a text-based PDF instead."
        elif status == 400 and detail == "AWS credentials are not configured for Textract.":
            code = "AWS_CREDENTIALS_MISSING"
            hint = "Set AWS_REGION, AWS_ACCESS_KEY_ID, and AWS_SECRET_ACCESS_KEY (or provide an AWS profile/role)."
        elif status == 400 and detail == "Textract rejected one or more uploaded documents.":
            code = "TEXTRACT_UNSUPPORTED_DOCUMENT"
            hint = "Try re-saving the PDF, or upload PNG/JPG images instead; Textract has strict format/size limits."
        elif status == 400 and detail == "Decision matrix missing for this mode.":
            code = "DECISION_MATRIX_MISSING"
            hint = "Set `TDF_DECISION_MATRIX_PATH` or switch modes."
        elif status == 400 and detail == "OPENAI_API_KEY is not set.":
            code = "OPENAI_API_KEY_MISSING"
            hint = "Set `OPENAI_API_KEY` in `.env` or your environment."
        elif status == 400 and detail == "Message is required.":
            code = "VALIDATION_ERROR"

        return _error(status, code, detail or "Request failed.", hint=hint)

    def _error(status_code: int, code: str, message: str, hint: Optional[str] = None) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content=ErrorResponse(error=ErrorDetail(code=code, message=message, hint=hint)).model_dump(),
        )

    def _require_session(session_id: str) -> SessionPaths:
        try:
            paths = get_session_paths(session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session_id.")
        if not paths.root.exists():
            raise HTTPException(status_code=404, detail="Session not found.")
        return paths

    def _session_status(paths: SessionPaths) -> SessionStatusResponse:
        meta = read_session_metadata(paths)
        docs: list[DocumentInfo] = []
        if paths.docs_dir.exists():
            for p in sorted(paths.docs_dir.iterdir(), key=lambda x: x.name.lower()):
                if not p.is_file():
                    continue
                stat = p.stat()
                docs.append(
                    DocumentInfo(
                        name=p.name,
                        size_bytes=int(stat.st_size),
                        modified_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    )
                )
        return SessionStatusResponse(
            session_id=paths.session_id,
            company=meta.get("company") if isinstance(meta, dict) else None,
            created_utc=meta.get("created_utc") if isinstance(meta, dict) else None,
            last_activity_utc=meta.get("last_activity_utc") if isinstance(meta, dict) else None,
            index_dirty=bool(meta.get("index_dirty", True)) if isinstance(meta, dict) else True,
            documents=docs,
        )

    def _index_status(paths: SessionPaths) -> IndexStatusResponse:
        meta = read_session_metadata(paths)
        dirty = bool(meta.get("index_dirty", True)) if isinstance(meta, dict) else True
        doc_count = 0
        if paths.docs_dir.exists():
            doc_count = sum(1 for p in paths.docs_dir.iterdir() if p.is_file())
        chroma_has_files = paths.chroma_dir.exists() and any(paths.chroma_dir.iterdir())
        if doc_count == 0:
            status = "missing"
        elif dirty or not chroma_has_files:
            status = "dirty"
        else:
            status = "ready"
        return IndexStatusResponse(status=status, index_dirty=dirty, document_count=doc_count)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> Any:
        now = datetime.now(timezone.utc).isoformat()
        return HealthResponse(
            status="ok",
            version=APP_VERSION,
            utc_time=now,
            python=platform.python_version(),
            platform=f"{platform.system()} {platform.release()}",
        )

    @app.post("/api/session", response_model=CreateSessionResponse)
    async def api_create_session(body: CreateSessionRequest) -> Any:
        paths = create_session(company=body.company)
        return CreateSessionResponse(session_id=paths.session_id)

    @app.get("/api/session/{session_id}", response_model=SessionStatusResponse)
    async def api_get_session(session_id: str) -> Any:
        paths = _require_session(session_id)
        touch_session(paths)
        return _session_status(paths)

    @app.delete("/api/session/{session_id}")
    async def api_delete_session(session_id: str) -> Any:
        paths = _require_session(session_id)
        try:
            delete_session(paths.session_id)
        except Exception:
            return _error(500, "INTERNAL_ERROR", "Failed to delete session.", hint="Check server logs.")
        return {"status": "deleted"}

    @app.post("/api/session/{session_id}/documents", response_model=SessionStatusResponse)
    async def api_upload_documents(
        session_id: str,
        files: list[UploadFile] = File(...),
    ) -> Any:
        paths = _require_session(session_id)
        shared_files_dir_raw = os.getenv("TDF_SHARED_FILES_DIR", "").strip()
        shared_files_dir = Path(shared_files_dir_raw) if shared_files_dir_raw else None
        if shared_files_dir:
            shared_files_dir.mkdir(parents=True, exist_ok=True)

        max_mb_raw = os.getenv("MAX_UPLOAD_MB", "100").strip()
        try:
            max_bytes = int(max_mb_raw) * 1024 * 1024
        except ValueError:
            max_bytes = 100 * 1024 * 1024

        total_written = 0
        for upload in files:
            original = upload.filename or "upload"
            safe = sanitize_filename(original)
            if not is_supported_filename(safe):
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {original}")
            target_name = dedupe_filename(paths.docs_dir, safe)
            target_path = paths.docs_dir / target_name

            written = 0
            try:
                with target_path.open("wb") as f:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        total_written += len(chunk)
                        if written > max_bytes or total_written > max_bytes:
                            raise HTTPException(status_code=413, detail="Upload too large.")
                        f.write(chunk)
            finally:
                await upload.close()

            if shared_files_dir:
                shared_name = dedupe_filename(shared_files_dir, target_name)
                shared_path = shared_files_dir / shared_name
                shared_path.write_bytes(target_path.read_bytes())

        set_index_dirty(paths, True, clear_chroma=True)
        clear_session_cache(paths.session_id)
        touch_session(paths)
        return _session_status(paths)

    @app.delete("/api/session/{session_id}/documents/{filename}", response_model=SessionStatusResponse)
    async def api_delete_document(session_id: str, filename: str) -> Any:
        paths = _require_session(session_id)
        safe = sanitize_filename(filename)
        doc_path = paths.docs_dir / safe
        if not doc_path.exists() or not doc_path.is_file():
            raise HTTPException(status_code=404, detail="Document not found.")
        try:
            doc_path.unlink()
        except Exception:
            return _error(500, "INTERNAL_ERROR", "Failed to delete document.", hint="Check permissions and retry.")
        set_index_dirty(paths, True, clear_chroma=True)
        clear_session_cache(paths.session_id)
        touch_session(paths)
        return _session_status(paths)

    @app.get("/api/session/{session_id}/index/status", response_model=IndexStatusResponse)
    async def api_index_status(session_id: str) -> Any:
        paths = _require_session(session_id)
        touch_session(paths)
        return _index_status(paths)

    @app.post("/api/session/{session_id}/analyze", response_model=AnalyzeResponse)
    async def api_analyze(session_id: str, body: AnalyzeRequest) -> Any:
        paths = _require_session(session_id)
        touch_session(paths)

        state = get_session_state(paths.session_id)
        async with state.lock:
            try:
                vectordb = await asyncio.to_thread(ensure_vectorstore, paths)
            except RuntimeError as e:
                msg = str(e)
                if msg == "NO_DOCUMENTS":
                    raise HTTPException(status_code=400, detail="No documents uploaded.")
                if msg == "NO_EXTRACTABLE_TEXT":
                    raise HTTPException(
                        status_code=400,
                        detail="No extractable text found in uploaded documents.",
                    )
                if msg == "AWS_CREDENTIALS_MISSING":
                    raise HTTPException(status_code=400, detail="AWS credentials are not configured for Textract.")
                if msg == "TEXTRACT_UNSUPPORTED_DOCUMENT":
                    raise HTTPException(status_code=400, detail="Textract rejected one or more uploaded documents.")
                if msg == "OPENAI_API_KEY_MISSING" or "OPENAI_API_KEY" in msg:
                    raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not set.")
                raise

            try:
                markdown = await asyncio.to_thread(
                    run_analysis,
                    company=body.company,
                    mode=body.mode,
                    vectordb=vectordb,
                    summarizer_spec=body.summarizer_spec,
                    analyzer_spec=body.analyzer_spec,
                )
            except FileNotFoundError:
                raise HTTPException(status_code=400, detail="Decision matrix missing for this mode.")
            except Exception as e:
                msg = str(e)
                if msg == "DECISION_MATRIX_MISSING":
                    raise HTTPException(status_code=400, detail="Decision matrix missing for this mode.")
                if "OPENAI_API_KEY" in msg or "api_key" in msg.lower():
                    raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not set.")
                raise

            output_id = f"o_{secrets.token_urlsafe(12)}"
            out_path = paths.outputs_dir / f"{output_id}.md"
            out_path.write_text(markdown, encoding="utf-8")
            return AnalyzeResponse(output_id=output_id, markdown=markdown)

    @app.post("/api/session/{session_id}/chat", response_model=ChatResponse)
    async def api_chat(session_id: str, body: ChatRequest) -> Any:
        paths = _require_session(session_id)
        touch_session(paths)

        if not body.message or not body.message.strip():
            raise HTTPException(status_code=400, detail="Message is required.")

        from storage.conversations import append_turn, load_conversation

        state = get_session_state(paths.session_id)
        async with state.lock:
            try:
                vectordb = await asyncio.to_thread(ensure_vectorstore, paths)
            except RuntimeError as e:
                msg = str(e)
                if msg == "NO_DOCUMENTS":
                    raise HTTPException(status_code=400, detail="No documents uploaded.")
                if msg == "NO_EXTRACTABLE_TEXT":
                    raise HTTPException(status_code=400, detail="No extractable text found in uploaded documents.")
                if msg == "AWS_CREDENTIALS_MISSING":
                    raise HTTPException(status_code=400, detail="AWS credentials are not configured for Textract.")
                if msg == "TEXTRACT_UNSUPPORTED_DOCUMENT":
                    raise HTTPException(status_code=400, detail="Textract rejected one or more uploaded documents.")
                if msg == "OPENAI_API_KEY_MISSING" or "OPENAI_API_KEY" in msg:
                    raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not set.")
                raise

            history = await asyncio.to_thread(load_conversation, paths, body.conversation_id)
            response = await asyncio.to_thread(
                run_chat,
                company=body.company,
                message=body.message,
                vectordb=vectordb,
                conversation_history=history,
                summarizer_spec=body.summarizer_spec,
                analyzer_spec=body.analyzer_spec,
            )
            await asyncio.to_thread(append_turn, paths, body.message, response, body.conversation_id)
            return ChatResponse(
                message=body.message,
                response=response,
                conversation_id=body.conversation_id,
            )

    @app.get("/api/session/{session_id}/outputs/{output_id}.md")
    async def api_download_output(session_id: str, output_id: str) -> Any:
        paths = _require_session(session_id)
        touch_session(paths)
        safe = sanitize_filename(output_id)
        file_path = paths.outputs_dir / f"{safe}.md"
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Output not found.")
        return FileResponse(path=str(file_path), media_type="text/markdown", filename=f"{safe}.md")

    frontend_dist = os.getenv("FRONTEND_DIST_DIR", "").strip()
    if frontend_dist and os.path.isdir(frontend_dist):
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
