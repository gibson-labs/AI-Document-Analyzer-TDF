import os
from typing import Any, Dict

import gradio as gr
from dotenv import load_dotenv

from file import (
    discover_documents,
    load_docs,
    build_vectorstore,
    make_llm_from_spec,
    make_rag_prompt,
    summarize_corpus,
    rag_answer,
    parse_decision_matrix,
    rag_weighted_analysis,
    fedex_review_analysis,
)


# Initialization (lazy build on first request to keep startup fast)
_state: Dict[str, Any] = {
    "vectordb": None,
}


def ensure_index(docs_dir: str, persist_dir: str):
    if _state.get("vectordb") is not None:
        return _state["vectordb"]
    doc_paths = discover_documents(docs_dir)
    if not doc_paths:
        raise RuntimeError(f"No supported documents found in {docs_dir}")
    docs = load_docs(doc_paths)
    _state["vectordb"] = build_vectorstore(docs, persist_dir)
    return _state["vectordb"]


def do_summarize(company: str, docs_dir: str, persist_dir: str, summarizer_spec: str) -> str:
    vectordb = ensure_index(docs_dir, persist_dir)
    summarizer_llm = make_llm_from_spec(summarizer_spec or None, default_openai_model="gpt-4o-mini")
    return summarize_corpus(company, vectordb, summarizer_llm)


def do_question(company: str, question: str, docs_dir: str, persist_dir: str, summarizer_spec: str, analyzer_spec: str) -> str:
    vectordb = ensure_index(docs_dir, persist_dir)
    prompt = make_rag_prompt()
    pre_summary = ""
    if summarizer_spec:
        pre_summary = summarize_corpus(company, vectordb, make_llm_from_spec(summarizer_spec, default_openai_model="gpt-4o-mini"))
    analyzer_llm = make_llm_from_spec(analyzer_spec or None, default_openai_model="gpt-4o-mini")
    return rag_answer(question, vectordb, prompt, analyzer_llm, pre_summary=pre_summary)


def do_weighted(company: str, decision_matrix_path: str, docs_dir: str, persist_dir: str, summarizer_spec: str, analyzer_spec: str) -> str:
    vectordb = ensure_index(docs_dir, persist_dir)
    prompt = make_rag_prompt()
    pre_summary = ""
    if summarizer_spec:
        pre_summary = summarize_corpus(company, vectordb, make_llm_from_spec(summarizer_spec, default_openai_model="gpt-4o-mini"))
    analyzer_llm = make_llm_from_spec(analyzer_spec or None, default_openai_model="gpt-4o-mini")
    if decision_matrix_path and os.path.exists(decision_matrix_path):
        weights = parse_decision_matrix(decision_matrix_path)
        return rag_weighted_analysis(company, vectordb, prompt, analyzer_llm, weights, pre_summary=pre_summary)
    return "Decision matrix not found. Please provide a valid path."


def do_fedex_review(company: str, docs_dir: str, persist_dir: str, summarizer_spec: str, analyzer_spec: str, decision_matrix_path: str) -> str:
    vectordb = ensure_index(docs_dir, persist_dir)
    analyzer_llm = make_llm_from_spec(analyzer_spec or None, default_openai_model="gpt-4o-mini")
    pre_summary = ""
    if summarizer_spec:
        pre_summary = summarize_corpus(company, vectordb, make_llm_from_spec(summarizer_spec, default_openai_model="gpt-4o-mini"))
    criteria_weights = None
    if decision_matrix_path and os.path.exists(decision_matrix_path):
        criteria_weights = parse_decision_matrix(decision_matrix_path)
    return fedex_review_analysis(company, vectordb, analyzer_llm, pre_summary=pre_summary, criteria_weights=criteria_weights)


def build_app() -> gr.Blocks:
    load_dotenv('/Users/johngibson/Documents/College/Fall 2025/Ai and Agentic/.env')

    default_docs = os.path.join(os.path.dirname(__file__), "documents")
    default_persist = os.path.join(os.path.dirname(__file__), "chroma")

    with gr.Blocks(title="Risk Analysis Assistant") as demo:
        gr.Markdown("""
        # Risk Analysis Assistant
        - Summarize your corpus, ask questions, or run weighted analysis.
        - Uses your OpenAI key if available; supports local Ollama models too.
        """)

        with gr.Accordion("Settings", open=False):
            docs_dir = gr.Textbox(label="Documents Directory", value=default_docs)
            persist_dir = gr.Textbox(label="Chroma Persist Directory", value=default_persist)
            company = gr.Textbox(label="Company", value="Target Company")
            summarizer = gr.Textbox(label="Summarizer LLM (e.g., openai:gpt-4o-mini or ollama:llama3)", value="openai:gpt-4o-mini")
            analyzer = gr.Textbox(label="Analyzer LLM (e.g., openai:gpt-4o-mini or ollama:llama3)", value="openai:gpt-4o-mini")

        with gr.Tab("Summarize"):
            sum_out = gr.Textbox(label="Summary", lines=18)
            sum_btn = gr.Button("Summarize Corpus")
            sum_btn.click(do_summarize, inputs=[company, docs_dir, persist_dir, summarizer], outputs=sum_out)

        with gr.Tab("Q&A"):
            question = gr.Textbox(label="Ask a question", placeholder="What risks are identified in the documents?", lines=2)
            qa_out = gr.Textbox(label="Answer", lines=18)
            qa_btn = gr.Button("Search and Answer")
            qa_btn.click(do_question, inputs=[company, question, docs_dir, persist_dir, summarizer, analyzer], outputs=qa_out)
        
        with gr.Tab("FedEx Review"):
            criteria_path = gr.Textbox(label="Optional Criteria Matrix (.xlsx)", value=os.path.join(default_docs, "Decision Matrix's.xlsx"))
            review_out = gr.Textbox(label="Safety & Compliance Review", lines=22)
            review_btn = gr.Button("Run FedEx Review")
            review_btn.click(do_fedex_review, inputs=[company, docs_dir, persist_dir, summarizer, analyzer, criteria_path], outputs=review_out)

        with gr.Tab("Weighted Analysis"):
            decision_path = gr.Textbox(label="Decision Matrix .xlsx Path", value=os.path.join(default_docs, "Decision Matrix's.xlsx"))
            w_out = gr.Textbox(label="Weighted Analysis Report", lines=22)
            w_btn = gr.Button("Run Weighted Analysis")
            w_btn.click(do_weighted, inputs=[company, decision_path, docs_dir, persist_dir, summarizer, analyzer], outputs=w_out)

    return demo


if __name__ == "__main__":
    app = build_app()
    # Share enabled per user preference to avoid localhost issues
    app.launch(share=True)
