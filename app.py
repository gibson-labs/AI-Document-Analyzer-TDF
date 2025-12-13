import os
from typing import Any, Dict, List, Tuple

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
    chatbot_answer,
    general_chat_answer,
)


# Initialization (lazy build on first request to keep startup fast)
_state: Dict[str, Any] = {
    "vectordb": None,
    "chatbot_history": {},  # Store conversation history per session
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


def do_chatbot(
    message: str,
    history: List,
    company: str,
    docs_dir: str,
    persist_dir: str,
    summarizer_spec: str,
    analyzer_spec: str,
) -> Tuple[List, str]:
    """Handle chatbot conversation with history.
    
    Returns:
        Tuple of (updated_history, empty_string) for Gradio Chatbot component
    """
    if not message or not message.strip():
        return history or [], ""
    
    if history is None:
        history = []
    
    try:
        analyzer_llm = make_llm_from_spec(analyzer_spec or None, default_openai_model="gpt-4o-mini")
        
        # Check if message is a simple greeting/small talk (skip document processing)
        simple_greetings = ["hello", "hi", "hey", "greetings", "good morning", "good afternoon", 
                           "good evening", "how are you", "what's up", "thanks", "thank you"]
        message_lower = message.lower().strip()
        is_simple_greeting = any(greeting in message_lower for greeting in simple_greetings) and len(message.split()) <= 5
        
        # For simple greetings or if no history and short message, use general chat (faster)
        if is_simple_greeting and not history:
            conversation_history = []
            response = general_chat_answer(
                question=message,
                llm=analyzer_llm,
                conversation_history=conversation_history,
            )
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": response})
            return history, ""
        
        # Try to use documents if available, otherwise use general chat
        try:
            vectordb = ensure_index(docs_dir, persist_dir)
            pre_summary = ""
            if summarizer_spec:
                pre_summary = summarize_corpus(company, vectordb, make_llm_from_spec(summarizer_spec, default_openai_model="gpt-4o-mini"))
            
            # Convert history to internal format [(user, bot), ...]
            conversation_history = []
            user_msg = None
            if history:
                for h in history:
                    if isinstance(h, dict):
                        # Messages format: {"role": "user", "content": "..."}
                        if h.get("role") == "user":
                            user_msg = h.get("content", "")
                        elif h.get("role") == "assistant" and user_msg:
                            conversation_history.append((user_msg, h.get("content", "")))
                            user_msg = None
                    elif isinstance(h, (list, tuple)) and len(h) == 2:
                        conversation_history.append((h[0], h[1]))
            
            response = chatbot_answer(
                question=message,
                vectordb=vectordb,
                llm=analyzer_llm,
                conversation_history=conversation_history,
                pre_summary=pre_summary,
                company=company,
            )
        except RuntimeError:
            # No documents available - use general chat
            conversation_history = []
            user_msg = None
            if history:
                for h in history:
                    if isinstance(h, dict):
                        if h.get("role") == "user":
                            user_msg = h.get("content", "")
                        elif h.get("role") == "assistant" and user_msg:
                            conversation_history.append((user_msg, h.get("content", "")))
                            user_msg = None
                    elif isinstance(h, (list, tuple)) and len(h) == 2:
                        conversation_history.append((h[0], h[1]))
            
            response = general_chat_answer(
                question=message,
                llm=analyzer_llm,
                conversation_history=conversation_history,
            )
        
        # Update history: Gradio Chatbot expects messages format [{"role": "user", "content": "..."}, ...]
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": response})
        return history, ""
    except Exception as e:
        error_str = str(e)
        # Provide specific guidance for common errors
        if "quota" in error_str.lower() or "429" in error_str or "insufficient_quota" in error_str:
            error_msg = (
                f"OpenAI API Quota Error: {error_str}\n\n"
                "Solutions:\n"
                "1. Check your OpenAI account billing: https://platform.openai.com/account/billing\n"
                "2. Add payment method or upgrade your plan\n"
                "3. Wait for your quota to reset (usually monthly)\n"
                "4. Use a different API key if available\n"
                "5. Switch to Ollama for local models (set analyzer to 'ollama:llama3' in Settings)"
            )
        elif "api key" in error_str.lower() or "authentication" in error_str.lower():
            error_msg = (
                f"API Key Error: {error_str}\n\n"
                "Troubleshooting:\n"
                "- Check if OPENAI_API_KEY is set in .env file\n"
                "- Verify the API key is valid and not expired\n"
                "- Ensure the .env file is in the project root directory"
            )
        else:
            error_msg = (
                f"Error: {error_str}\n\n"
                "Troubleshooting:\n"
                "- Check if OPENAI_API_KEY is set in .env file\n"
                "- Verify documents directory exists: {docs_dir}\n"
                "- Ensure documents are in the specified directory\n"
                "- Check OpenAI API status: https://status.openai.com"
            )
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": error_msg})
        return history, ""


def build_app() -> gr.Blocks:
    # Try to load .env from project root, fallback to absolute path if needed
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        # Fallback: try to load from environment variable or default location
        load_dotenv()

    default_docs = os.path.join(os.path.dirname(__file__), "document_loader")
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
        
        with gr.Tab("Chatbot"):
            gr.Markdown("""
            ### Conversational Assistant
            Ask questions about the documents. The chatbot remembers 
            previous messages in this session, so you can ask follow-up questions.
            """)
            chatbot = gr.Chatbot(label="Conversation", height=500)
            with gr.Row():
                chatbot_msg = gr.Textbox(
                    label="Your message",
                    placeholder="Ask a question about the documents...",
                    lines=2,
                    show_label=False,
                    scale=4,
                    container=False
                )
                chatbot_submit_btn = gr.Button("Send", variant="primary", scale=1)
            chatbot_clear = gr.Button("Clear Conversation", variant="secondary")
            
            chatbot_msg.submit(
                do_chatbot,
                inputs=[chatbot_msg, chatbot, company, docs_dir, persist_dir, summarizer, analyzer],
                outputs=[chatbot, chatbot_msg]
            )
            chatbot_submit_btn.click(
                do_chatbot,
                inputs=[chatbot_msg, chatbot, company, docs_dir, persist_dir, summarizer, analyzer],
                outputs=[chatbot, chatbot_msg]
            )
            
            chatbot_clear.click(lambda: [], outputs=[chatbot])
        
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
