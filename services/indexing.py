import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from storage.sessions import SessionPaths, read_session_metadata, set_index_dirty
from services.state import clear_session_cache, get_session_state


_WORD_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


@dataclass(frozen=True)
class _ScoredDoc:
    score: int
    doc: Document


class InMemoryRetriever:
    def __init__(self, docs: list[Document], *, k: int = 8):
        self._docs = docs
        self._k = k
        self._doc_tokens = [_tokenize(d.page_content) for d in docs]

    def invoke(self, query: str) -> list[Document]:
        q = _tokenize(query)
        if not q:
            return self._docs[: self._k]
        scored: list[_ScoredDoc] = []
        for d, tokens in zip(self._docs, self._doc_tokens, strict=True):
            score = len(q.intersection(tokens))
            if score <= 0:
                continue
            scored.append(_ScoredDoc(score=score, doc=d))
        scored.sort(key=lambda s: s.score, reverse=True)
        return [s.doc for s in scored[: self._k]]


class InMemoryVectorDB:
    def __init__(self, docs: list[Document]):
        self._docs = docs

    def as_retriever(self, search_type: str = "mmr", search_kwargs: dict[str, Any] | None = None) -> InMemoryRetriever:
        k = 8
        if search_kwargs and isinstance(search_kwargs.get("k"), int):
            k = int(search_kwargs["k"])
        return InMemoryRetriever(self._docs, k=k)


def _iter_uploaded_files(docs_dir: Path) -> Iterable[Path]:
    for p in sorted(docs_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file():
            yield p


def _extract_to_text_dir(paths: SessionPaths) -> list[Path]:
    docs_dir = paths.docs_dir
    if not docs_dir.exists():
        raise RuntimeError("NO_DOCUMENTS")

    extracted_dir = paths.root / "extracted_text"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    shared_extracted_raw = os.getenv("TDF_SHARED_EXTRACTED_TEXT_DIR", "").strip()
    shared_extracted_dir = Path(shared_extracted_raw) if shared_extracted_raw else None
    if shared_extracted_dir:
        shared_extracted_dir.mkdir(parents=True, exist_ok=True)

    # Import here to avoid side effects at server import time.
    from text_extraction import extract_image_text, extract_pdf_text, extract_xlsx_text

    def _is_aws_cred_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "unable to locate credentials" in msg
            or "aws textract is not configured" in msg
            or "check aws credentials" in msg
            or "credentials" in msg
        )

    def _is_unsupported_document(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "unsupporteddocumentexception" in msg or "unsupported document format" in msg

    outputs: list[Path] = []
    for src in _iter_uploaded_files(docs_dir):
        ext = src.suffix.lower()
        text = ""
        if ext == ".pdf":
            try:
                text = extract_pdf_text(src)
            except Exception as e:
                if _is_aws_cred_error(e):
                    raise RuntimeError("AWS_CREDENTIALS_MISSING") from e
                if _is_unsupported_document(e):
                    raise RuntimeError("TEXTRACT_UNSUPPORTED_DOCUMENT") from e
                raise
        elif ext in {".jpg", ".jpeg", ".png"}:
            try:
                text = extract_image_text(src)
            except Exception as e:
                if _is_aws_cred_error(e):
                    raise RuntimeError("AWS_CREDENTIALS_MISSING") from e
                if _is_unsupported_document(e):
                    raise RuntimeError("TEXTRACT_UNSUPPORTED_DOCUMENT") from e
                raise
        elif ext in {".xlsx", ".xls"}:
            text = extract_xlsx_text(src)
        else:
            continue

        if text and text.strip():
            out = extracted_dir / f"{src.name}.txt"
            out.write_text(text.strip() + "\n", encoding="utf-8")
            outputs.append(out)
            if shared_extracted_dir:
                shared_out = shared_extracted_dir / out.name
                shared_out.write_text(text.strip() + "\n", encoding="utf-8")
    return outputs


def _build_vectordb_from_extracted(paths: SessionPaths) -> InMemoryVectorDB:
    extracted_dir = paths.root / "extracted_text"
    if not extracted_dir.exists():
        raise RuntimeError("NO_EXTRACTABLE_TEXT")

    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=200)
    docs: list[Document] = []
    for p in sorted(extracted_dir.glob("*.txt"), key=lambda x: x.name.lower()):
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        # extracted text files are named like: "<original_filename>.<ext>.txt"
        original_name = p.name[:-4] if p.name.lower().endswith(".txt") else p.name
        base = Document(page_content=text, metadata={"source": original_name})
        docs.extend(splitter.split_documents([base]))

    if not docs:
        raise RuntimeError("NO_EXTRACTABLE_TEXT")
    return InMemoryVectorDB(docs)


def rebuild_vectorstore(paths: SessionPaths) -> Any:
    doc_paths = list(_iter_uploaded_files(paths.docs_dir))
    if not doc_paths:
        raise RuntimeError("NO_DOCUMENTS")

    extracted = _extract_to_text_dir(paths)
    if not extracted:
        raise RuntimeError("NO_EXTRACTABLE_TEXT")

    vectordb = _build_vectordb_from_extracted(paths)
    set_index_dirty(paths, False)
    clear_session_cache(paths.session_id)
    return vectordb


def ensure_vectorstore(paths: SessionPaths) -> Any:
    state = get_session_state(paths.session_id)
    if state.vectordb is not None:
        return state.vectordb

    meta = read_session_metadata(paths)
    dirty = bool(meta.get("index_dirty", True)) if isinstance(meta, dict) else True
    if dirty:
        state.vectordb = rebuild_vectorstore(paths)
        return state.vectordb

    extracted_dir = paths.root / "extracted_text"
    if not extracted_dir.exists():
        state.vectordb = rebuild_vectorstore(paths)
    else:
        state.vectordb = _build_vectordb_from_extracted(paths)
    return state.vectordb
