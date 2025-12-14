import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class SessionRuntimeState:
    lock: asyncio.Lock
    vectordb: Any | None = None
    pre_summary: str | None = None


_SESSION_STATE: dict[str, SessionRuntimeState] = {}


def get_session_state(session_id: str) -> SessionRuntimeState:
    state = _SESSION_STATE.get(session_id)
    if state is None:
        state = SessionRuntimeState(lock=asyncio.Lock())
        _SESSION_STATE[session_id] = state
    return state


def clear_session_cache(session_id: str) -> None:
    state = _SESSION_STATE.get(session_id)
    if not state:
        return
    state.vectordb = None
    state.pre_summary = None

