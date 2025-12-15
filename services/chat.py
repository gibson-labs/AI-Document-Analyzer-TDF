from __future__ import annotations

from typing import Any, Optional

from file import chatbot_answer, make_llm_from_spec, summarize_corpus


def run_chat(
    *,
    company: str,
    message: str,
    vectordb: Any,
    conversation_history: list[tuple[str, str]],
    summarizer_spec: Optional[str],
    analyzer_spec: Optional[str],
) -> str:
    analyzer_llm = make_llm_from_spec(analyzer_spec or None, default_openai_model="gpt-4o-mini")
    summarizer_llm = make_llm_from_spec(summarizer_spec or None, default_openai_model="gpt-4o-mini") if summarizer_spec else None
    pre_summary = summarize_corpus(company, vectordb, summarizer_llm) if summarizer_llm else ""
    return chatbot_answer(
        question=message,
        vectordb=vectordb,
        llm=analyzer_llm,
        conversation_history=conversation_history,
        pre_summary=pre_summary,
        company=company,
    )

