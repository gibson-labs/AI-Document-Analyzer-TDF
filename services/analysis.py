from __future__ import annotations

from typing import Any, Optional

from file import (
    fedex_review_analysis,
    make_llm_from_spec,
    make_rag_prompt,
    rag_answer,
    rag_weighted_analysis,
    summarize_corpus,
)

from services.decision_matrix import maybe_load_weights
from services.markdown import simple_report_to_markdown


def run_analysis(
    *,
    company: str,
    mode: str,
    vectordb: Any,
    summarizer_spec: Optional[str],
    analyzer_spec: Optional[str],
) -> str:
    analyzer_llm = make_llm_from_spec(analyzer_spec or None, default_openai_model="gpt-4o-mini")
    summarizer_llm = make_llm_from_spec(summarizer_spec or None, default_openai_model="gpt-4o-mini") if summarizer_spec else None

    pre_summary = summarize_corpus(company, vectordb, summarizer_llm) if summarizer_llm else ""
    prompt = make_rag_prompt()

    weights = maybe_load_weights(mode)

    if mode == "weighted":
        if not weights:
            raise RuntimeError("DECISION_MATRIX_MISSING")
        text = rag_weighted_analysis(company, vectordb, prompt, analyzer_llm, weights, pre_summary=pre_summary)
        return simple_report_to_markdown(text)

    if mode == "memo":
        user_query = (
            f"Analyze the provided materials for {company}. Identify credit risk factors, liquidity, profitability, leverage, compliance issues, operational risks, customer concentration, and industry outlook. Provide a 1-5 risk score and an approve/decline loan recommendation with rationale and citations."
        )
        text = rag_answer(user_query, vectordb, prompt, analyzer_llm, pre_summary=pre_summary)
        return simple_report_to_markdown(text)

    text = fedex_review_analysis(company, vectordb, analyzer_llm, pre_summary=pre_summary, criteria_weights=weights)
    return simple_report_to_markdown(text)

