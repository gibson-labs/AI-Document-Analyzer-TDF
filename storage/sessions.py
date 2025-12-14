import asyncio
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SUPPORTED_FILE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".xls"}


DEFAULT_SESSIONS_ROOT = "/tmp/tdf_sessions"
DEFAULT_SESSION_TTL_MINUTES = 60
DEFAULT_CLEANUP_INTERVAL_SECONDS = 60

_SESSION_ID_RE = re.compile(r"^s_[A-Za-z0-9_-]{8,64}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_isoformat(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def sessions_root() -> Path:
    return Path(os.getenv("TDF_SESSIONS_ROOT", DEFAULT_SESSIONS_ROOT))


def session_ttl_minutes() -> int:
    raw = os.getenv("SESSION_TTL_MINUTES", str(DEFAULT_SESSION_TTL_MINUTES)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SESSION_TTL_MINUTES


def cleanup_interval_seconds() -> int:
    raw = os.getenv("SESSION_CLEANUP_INTERVAL_SECONDS", str(DEFAULT_CLEANUP_INTERVAL_SECONDS)).strip()
    try:
        return max(5, int(raw))
    except ValueError:
        return DEFAULT_CLEANUP_INTERVAL_SECONDS


def new_session_id() -> str:
    return f"s_{secrets.token_urlsafe(12)}"


def validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id or ""):
        raise ValueError("Invalid session_id format.")


def sanitize_filename(filename: str) -> str:
    name = (filename or "").strip()
    name = name.replace("\x00", "")
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[\r\n\t]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "upload"
    return name[:200]


def is_supported_filename(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in SUPPORTED_FILE_EXTS


def dedupe_filename(directory: Path, filename: str) -> str:
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = filename
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{base} ({counter}){ext}"
        counter += 1
    return candidate


def ensure_within_root(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if root_resolved == candidate_resolved:
        return candidate_resolved
    if root_resolved not in candidate_resolved.parents:
        raise ValueError("Path escapes session root.")
    return candidate_resolved


@dataclass(frozen=True)
class SessionPaths:
    session_id: str
    root: Path
    docs_dir: Path
    chroma_dir: Path
    outputs_dir: Path
    conversations_dir: Path
    metadata_path: Path


def get_session_paths(session_id: str, root_dir: Optional[Path] = None) -> SessionPaths:
    validate_session_id(session_id)
    root_dir = root_dir or sessions_root()
    session_root = ensure_within_root(root_dir, root_dir / session_id)
    return SessionPaths(
        session_id=session_id,
        root=session_root,
        docs_dir=session_root / "docs",
        chroma_dir=session_root / "chroma",
        outputs_dir=session_root / "outputs",
        conversations_dir=session_root / "conversations",
        metadata_path=session_root / "session.json",
    )


def _ensure_dirs(paths: SessionPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.docs_dir.mkdir(parents=True, exist_ok=True)
    paths.chroma_dir.mkdir(parents=True, exist_ok=True)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    paths.conversations_dir.mkdir(parents=True, exist_ok=True)


def read_session_metadata(paths: SessionPaths) -> dict[str, Any]:
    if not paths.metadata_path.exists():
        return {}
    try:
        return json.loads(paths.metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_session_metadata(paths: SessionPaths, metadata: dict[str, Any]) -> None:
    _ensure_dirs(paths)
    paths.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def touch_session(paths: SessionPaths) -> None:
    meta = read_session_metadata(paths)
    meta.setdefault("session_id", paths.session_id)
    meta["last_activity_utc"] = isoformat(utc_now())
    write_session_metadata(paths, meta)


def set_index_dirty(paths: SessionPaths, dirty: bool = True, *, clear_chroma: bool = False) -> None:
    meta = read_session_metadata(paths)
    meta.setdefault("session_id", paths.session_id)
    meta["index_dirty"] = bool(dirty)
    meta["last_activity_utc"] = isoformat(utc_now())
    write_session_metadata(paths, meta)
    if clear_chroma and paths.chroma_dir.exists():
        shutil.rmtree(paths.chroma_dir, ignore_errors=True)
        paths.chroma_dir.mkdir(parents=True, exist_ok=True)


def create_session(company: Optional[str] = None, root_dir: Optional[Path] = None) -> SessionPaths:
    root_dir = root_dir or sessions_root()
    root_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(5):
        session_id = new_session_id()
        paths = get_session_paths(session_id, root_dir=root_dir)
        if not paths.root.exists():
            _ensure_dirs(paths)
            meta: dict[str, Any] = {
                "session_id": session_id,
                "created_utc": isoformat(utc_now()),
                "last_activity_utc": isoformat(utc_now()),
            }
            if company:
                meta["company"] = company
            meta["index_dirty"] = True
            write_session_metadata(paths, meta)
            return paths
    raise RuntimeError("Failed to allocate a new session_id; please retry.")


def delete_session(session_id: str, root_dir: Optional[Path] = None) -> None:
    paths = get_session_paths(session_id, root_dir=root_dir)
    if paths.root.exists():
        shutil.rmtree(paths.root)


def list_sessions(root_dir: Optional[Path] = None) -> list[str]:
    root_dir = root_dir or sessions_root()
    if not root_dir.exists():
        return []
    session_ids: list[str] = []
    for child in root_dir.iterdir():
        if not child.is_dir():
            continue
        sid = child.name
        if _SESSION_ID_RE.match(sid):
            session_ids.append(sid)
    return sorted(session_ids)


def _session_last_activity(paths: SessionPaths) -> Optional[datetime]:
    meta = read_session_metadata(paths)
    raw = meta.get("last_activity_utc")
    if isinstance(raw, str) and raw.strip():
        try:
            return parse_isoformat(raw)
        except Exception:
            return None
    return None


def cleanup_expired_sessions(
    root_dir: Optional[Path] = None,
    ttl_minutes: Optional[int] = None,
    now: Optional[datetime] = None,
) -> int:
    root_dir = root_dir or sessions_root()
    ttl_minutes = ttl_minutes or session_ttl_minutes()
    now = now or utc_now()
    cutoff = now - timedelta(minutes=ttl_minutes)
    removed = 0
    for session_id in list_sessions(root_dir=root_dir):
        paths = get_session_paths(session_id, root_dir=root_dir)
        last_activity = _session_last_activity(paths) or utc_now()
        if last_activity < cutoff:
            try:
                delete_session(session_id, root_dir=root_dir)
                removed += 1
            except Exception:
                continue
    return removed


async def ttl_cleanup_loop(stop: asyncio.Event) -> None:
    interval = cleanup_interval_seconds()
    while not stop.is_set():
        try:
            cleanup_expired_sessions()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
