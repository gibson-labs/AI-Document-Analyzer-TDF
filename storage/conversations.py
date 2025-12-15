import json
import re
from pathlib import Path

from storage.sessions import SessionPaths, ensure_within_root, sanitize_filename


_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def conversation_file(paths: SessionPaths, conversation_id: str = "default") -> Path:
    cid = (conversation_id or "default").strip()
    if not _CONV_ID_RE.match(cid):
        cid = "default"
    filename = sanitize_filename(f"{cid}.json")
    candidate = paths.conversations_dir / filename
    return ensure_within_root(paths.root, candidate)


def load_conversation(paths: SessionPaths, conversation_id: str = "default") -> list[tuple[str, str]]:
    p = conversation_file(paths, conversation_id)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    history: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        user = item.get("user")
        assistant = item.get("assistant")
        if isinstance(user, str) and isinstance(assistant, str):
            history.append((user, assistant))
    return history


def save_conversation(paths: SessionPaths, items: list[tuple[str, str]], conversation_id: str = "default") -> None:
    p = conversation_file(paths, conversation_id)
    payload = [{"user": u, "assistant": a} for u, a in items]
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_turn(paths: SessionPaths, user: str, assistant: str, conversation_id: str = "default") -> list[tuple[str, str]]:
    items = load_conversation(paths, conversation_id)
    items.append((user, assistant))
    save_conversation(paths, items, conversation_id)
    return items

