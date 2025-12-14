import os
import json
import argparse
from typing import List, Any, Dict, Tuple, Optional

# Import all required dependencies
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, UnstructuredImageLoader
from langchain_community.document_loaders import UnstructuredExcelLoader, UnstructuredFileLoader
from langchain_core.prompts import ChatPromptTemplate
import pandas as pd
from dotenv import load_dotenv

# Optional Ollama support for local summarizer/analyzer
try:
    from langchain_ollama import ChatOllama  # type: ignore
except Exception:
    ChatOllama = None  # noqa: N816 - optional runtime import


SUPPORTED_FILE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".xls"}

SECTION_DEFINITIONS = {
    "safety_performance": {
        "title": "Safety Performance",
        "query": "SRS Report - LH accident preventable roadside inspection SRI trend",
    },
    "camera_behavior": {
        "title": "Camera / Behavior Compliance",
        "query": "VEDR KI dashboard safety score Spotlight camera behavior deduction speeding hard braking",
    },
    "availability_reliability": {
        "title": "Availability & Operational Reliability",
        "query": "Availability percent declines tractor uptime Spotlight availability report",
    },
    "fleet_quality": {
        "title": "Fleet Quality & Compliance",
        "query": "Schedule B Tractor Roster model year odometer compliant age limit",
    },
    "contract_structure": {
        "title": "Contract / Run Structure Stability",
        "query": "Schedule A assigned runs structure dedicated lanes",
    },
}

SCORE_COMPONENT_KEYS = [
    "safety_performance",
    "camera_behavior",
    "availability_reliability",
    "fleet_quality",
]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def determine_risk_band(score: float) -> str:
    if score >= 70:
        return "High"
    if score >= 45:
        return "Moderate"
    return "Low"


def collect_section_context(vectordb: Any, k: int = 6) -> Dict[str, str]:
    contexts: Dict[str, str] = {}
    retriever = vectordb.as_retriever(search_type="mmr", search_kwargs={"k": k})
    for key, meta in SECTION_DEFINITIONS.items():
        query = meta["query"]
        docs = retriever.invoke(query)
        blocks: List[str] = []
        for d in docs:
            src = d.metadata.get("source", "unknown") if hasattr(d, "metadata") else "unknown"
            page = d.metadata.get("page") if hasattr(d, "metadata") else None
            page_note = f" (page {page})" if page is not None else ""
            snippet = d.page_content.strip()
            if not snippet:
                continue
            blocks.append(f"Source: {src}{page_note}\n{snippet}\n")
        contexts[key] = "\n---\n".join(blocks) if blocks else ""
    return contexts


def make_fedex_review_prompt() -> Any:
    schema = (
        "Respond with JSON only using these keys:\n"
        "- overall_summary: 60-120 words covering the whole FedEx package.\n"
        "- sections: object holding safety_performance, camera_behavior, availability_reliability, "
        "fleet_quality, contract_structure. Each section must include 'summary' (3-5 sentences with citations) "
        "and 'documents' (array of objects with 'name' and 'reason' explaining how that document impacted the score).\n"
        "- score_components: numeric 0-25 integers for safety_performance, camera_behavior, availability_reliability, fleet_quality.\n"
        "- safety_compliance_score: integer 0-100 equal to the sum of the score_components.\n"
        "- risk_band: Low, Moderate, or High.\n"
        "- trend: Improving, Stable, Deteriorating, or Unknown.\n"
        "- justifications: array of 3-6 bullet strings with filename/page citations.\n"
        "- document_breakdown: object keyed by document filename, each containing "
        "\"role\" (1-2 sentences describing why it matters) and 'impacts' "
        "(array noting which sections or criteria it informed).\n"
    )
    system_instructions = (
        "You are a FedEx Ground safety and compliance reviewer. "
        "Review only the provided FedEx package materials (SRS, Spotlight, VEDR/KI, availability, Schedule A/B, "
        "tractor roster) over the most recent 12-13 months. "
        "Assess five areas exactly as described: "
        "1) Safety Performance (SRS Report - LH, SRI level/trend, accident preventability patterns, roadside violations). "
        "2) Camera / Behavior compliance (VEDR/KI dashboards, Spotlight medals, safety score, top negative behaviors, PASS/FAIL). "
        "3) Availability & operational reliability (tractor availability %, declines, per-unit gaps). "
        "4) Fleet quality & compliance (Schedule B, tractor roster, age vs FedEx eligibility). "
        "5) Contract/run structure stability (Schedule A assignments, assigned vs unassigned mix, coverage vs fleet). "
        "Discount non-preventable incidents, punish repeated preventables or disconnects. "
        "Flag any VEDR fails or risky camera trends even if accidents are clean. "
        "Relate availability to revenue risk. "
        "Tie fleet age-outs to Schedule A coverage. "
        "If a criteria list from a decision matrix is provided, reference those criterion names when you describe impacts and in document_breakdown.impacts. "
        "Do not invent data; cite filenames with page/row when provided."
    )
    return ChatPromptTemplate.from_messages([
        ("system", system_instructions),
        (
            "human",
            "Company: {company}\n\nContext by section:\n{context}\n\n"
            "Task: Follow the evaluation script, populate every section, and respond with JSON only.\n"
            f"Schema requirements: {schema}"
        ),
    ])


def discover_documents(root_dir: str) -> List[str]:
    files: List[str] = []
    for entry in os.listdir(root_dir):
        full = os.path.join(root_dir, entry)
        if not os.path.isfile(full):
            continue
        _, ext = os.path.splitext(entry)
        if ext.lower() in SUPPORTED_FILE_EXTS:
            files.append(full)
    return sorted(files)


def load_docs(paths: List[str]) -> List[Any]:
    docs: List[Any] = []
    skipped_images = []
    
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            # Try PyPDFLoader first (fast for text-based PDFs)
            try:
                loader = PyPDFLoader(path)
                pdf_docs = loader.load()
                # Check if we got any content
                has_content = any(hasattr(d, 'page_content') and d.page_content.strip() for d in pdf_docs)
                if has_content:
                    docs.extend(pdf_docs)
                else:
                    # Fall back to UnstructuredFileLoader for OCR (slower but handles image-based PDFs)
                    print(f"PDF {os.path.basename(path)} appears to be image-based, using OCR extraction...")
                    try:
                        loader = UnstructuredFileLoader(path, strategy="hi_res")
                        pdf_docs = loader.load()
                        docs.extend(pdf_docs)
                        print(f"Successfully extracted text from {os.path.basename(path)} using OCR")
                    except Exception as ocr_error:
                        print(f"Warning: OCR extraction failed for {os.path.basename(path)}: {ocr_error}")
                        # Still add empty docs to maintain structure, but they'll be filtered later
                        docs.extend(pdf_docs)
            except Exception as e:
                print(f"Error loading PDF {os.path.basename(path)}: {e}")
                # Try UnstructuredFileLoader as fallback
                try:
                    print(f"Attempting OCR extraction for {os.path.basename(path)}...")
                    loader = UnstructuredFileLoader(path, strategy="hi_res")
                    pdf_docs = loader.load()
                    docs.extend(pdf_docs)
                except Exception as fallback_error:
                    print(f"Failed to load {os.path.basename(path)} with both methods: {fallback_error}")
        elif ext in {".jpg", ".jpeg", ".png"}:
            try:
                loader = UnstructuredImageLoader(path)
                docs.extend(loader.load())
            except Exception as e:
                if "tesseract" in str(e).lower():
                    skipped_images.append(path)
                    print(f"Skipping image {path} - Tesseract not available")
                else:
                    raise e
        elif ext in {".xlsx", ".xls"}:
            loader = UnstructuredExcelLoader(path)
            docs.extend(loader.load())
    
    if skipped_images:
        print(f"\nNote: Skipped {len(skipped_images)} image files due to missing Tesseract OCR:")
        for img in skipped_images:
            print(f"  - {os.path.basename(img)}")
        print("To process images, install Tesseract: brew install tesseract\n")
    
    # Attach source metadata
    for d in docs:
        if hasattr(d, "metadata") and "source" not in d.metadata:
            d.metadata["source"] = d.metadata.get("file_path") or d.metadata.get("source") or "unknown"
    return docs


def build_vectorstore(docs: List[Any], persist_dir: str) -> Any:
    if not docs:
        raise ValueError("No documents provided to build vectorstore. Please ensure documents are loaded correctly.")
    
    # Check if documents have content
    docs_with_content = [d for d in docs if hasattr(d, 'page_content') and d.page_content.strip()]
    if not docs_with_content:
        raise ValueError(
            "All documents appear to be empty or could not be parsed. "
            "Please check that your documents are valid and contain readable content."
        )
    
    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=200)
    splits = splitter.split_documents(docs_with_content)
    
    # Filter out empty splits
    splits = [s for s in splits if hasattr(s, 'page_content') and s.page_content.strip()]
    
    if not splits:
        raise ValueError(
            "Document splitting resulted in no content. This may indicate:\n"
            "- Documents are empty or corrupted\n"
            "- Text extraction failed\n"
            "- Check that OPENAI_API_KEY is set for embedding generation"
        )
    
    # Check for OpenAI API key before attempting embeddings
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please set it in your .env file or environment.\n"
            "The vectorstore requires OpenAI embeddings to index your documents."
        )
    
    try:
        embeddings = OpenAIEmbeddings()
        vectordb = Chroma.from_documents(splits, embedding=embeddings, persist_directory=persist_dir)
        # Note: Chroma 0.4.x+ automatically persists, no need to call persist()
        return vectordb
    except Exception as e:
        if "empty" in str(e).lower() or "[]" in str(e):
            raise ValueError(
                f"Failed to create embeddings: {str(e)}\n\n"
                "This usually means:\n"
                "- Documents were loaded but contain no extractable text\n"
                "- OPENAI_API_KEY is invalid or missing\n"
                "- Network issues preventing API calls\n\n"
                "Please check your documents and API key configuration."
            ) from e
        raise


def make_rag_prompt() -> Any:
    return ChatPromptTemplate.from_messages([
        (
            "system",
            (
                "You are a credit risk analyst. Given retrieved snippets from company documents "
                "(10-K style, spreadsheets, schedules, spotlights, availability reports), produce a "
                "professional credit memo.\n\n"
                "Deliverables:\n"
                "1) Concise risk summary\n"
                "2) Key risk drivers with evidence quotes and sources\n"
                "3) A 1-5 risk score (1=very low risk, 5=very high risk)\n"
                "4) A loan recommendation (approve/decline) with rationale\n\n"
                "Guidelines:\n"
                "- Be specific, cite sources by filename and page/row if present.\n"
                "- Note inconsistencies between documents.\n"
                "- If information is missing, state assumptions."
            ),
        ),
        (
            "human",
            (
                "Using only the context below, complete the task.\n\n"
                "Context:\n{context}\n\n"
                "Task:\n{task}"
            ),
        ),
    ])


def make_chatbot_prompt() -> Any:
    """Create a conversational prompt for chatbot mode that's more natural and interactive."""
    return ChatPromptTemplate.from_messages([
        (
            "system",
            (
                "You are a helpful and knowledgeable credit risk analysis assistant. You help users "
                "understand risk factors, financial metrics, compliance issues, and other aspects "
                "of company documents through natural conversation.\n\n"
                "Guidelines:\n"
                "- Answer questions clearly and conversationally\n"
                "- Cite specific sources (filename and page/row) when referencing documents\n"
                "- If you don't know something from the context, say so\n"
                "- You can ask clarifying questions if needed\n"
                "- Be professional but friendly in tone\n"
                "- When discussing risks or financial metrics, provide context and explain significance"
            ),
        ),
        (
            "human",
            (
                "Context from documents:\n{context}\n\n"
                "Conversation history:\n{history}\n\n"
                "User question: {question}\n\n"
                "Please provide a helpful answer based on the context above. If the question refers "
                "to previous conversation, use the history to provide context-aware responses."
            ),
        ),
    ])


def make_llm_from_spec(spec: Optional[str], default_openai_model: str = "gpt-4o-mini") -> Any:
    """Create an LLM from a provider spec like 'openai:gpt-4o-mini' or 'ollama:llama3'.

    If spec is None, returns OpenAI with default model.
    """
    if not spec:
        return ChatOpenAI(model=default_openai_model, temperature=0.2)
    provider, _, name = spec.partition(":")
    provider = provider.strip().lower() or "openai"
    name = name.strip() or default_openai_model
    if provider == "openai":
        return ChatOpenAI(model=name, temperature=0.2)
    if provider == "ollama":
        if ChatOllama is None:
            raise RuntimeError("Ollama LLM requested but langchain_ollama is not installed.")
        return ChatOllama(model=name, temperature=0.2)
    raise ValueError(f"Unknown LLM provider: {provider}. Use 'openai:' or 'ollama:'.")


def rag_answer(query: str, vectordb: Any, prompt: Any, llm: Any, pre_summary: str = "") -> str:
    retriever = vectordb.as_retriever(search_type="mmr", search_kwargs={"k": 8})
    context_docs = retriever.invoke(query)
    context_blocks = []
    if pre_summary:
        context_blocks.append(f"Pre-summary (from summarizer):\n{pre_summary}\n")
    for d in context_docs:
        src = d.metadata.get("source", "unknown") if hasattr(d, "metadata") else "unknown"
        page = d.metadata.get("page", None) if hasattr(d, "metadata") else None
        page_note = f" (page {page})" if page is not None else ""
        context_blocks.append(f"Source: {src}{page_note}\n{d.page_content}\n")
    context = "\n---\n".join(context_blocks)

    chain = prompt | llm
    task = (
        "Produce the risk summary, key drivers with citations, a 1-5 risk score, "
        "and an approve/decline recommendation with rationale."
    )
    resp = chain.invoke({"context": context, "task": task})
    return getattr(resp, "content", str(resp))


def general_chat_answer(
    question: str,
    llm: Any,
    conversation_history: List[Tuple[str, str]] = None,
) -> str:
    """Answer a question in general chat mode without document context.
    
    Args:
        question: The user's question
        llm: The language model to use
        conversation_history: List of (user_message, assistant_message) tuples
    
    Returns:
        The assistant's response
    """
    if conversation_history is None:
        conversation_history = []
    
    # Build conversation history string
    history_str = ""
    if conversation_history:
        history_lines = []
        for user_msg, assistant_msg in conversation_history[-5:]:  # Last 5 exchanges for context
            history_lines.append(f"User: {user_msg}")
            history_lines.append(f"Assistant: {assistant_msg}")
        history_str = "\n".join(history_lines)
    else:
        history_str = "No previous conversation."
    
    # Create a general chat prompt (no document context)
    general_prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            (
                "You are a helpful and knowledgeable assistant. You can answer questions, "
                "have conversations, and provide information on a wide variety of topics.\n\n"
                "Guidelines:\n"
                "- Answer questions clearly and conversationally\n"
                "- Be helpful, friendly, and professional\n"
                "- If you don't know something, say so\n"
                "- You can ask clarifying questions if needed\n"
                "- Use the conversation history to provide context-aware responses"
            ),
        ),
        (
            "human",
            (
                "Conversation history:\n{history}\n\n"
                "User question: {question}\n\n"
                "Please provide a helpful answer. If the question refers to previous conversation, "
                "use the history to provide context-aware responses."
            ),
        ),
    ])
    
    chain = general_prompt | llm
    resp = chain.invoke({
        "history": history_str,
        "question": question,
    })
    return getattr(resp, "content", str(resp))


def chatbot_answer(
    question: str,
    vectordb: Any,
    llm: Any,
    conversation_history: List[Tuple[str, str]] = None,
    pre_summary: str = "",
    company: str = "Target Company",
) -> str:
    """Answer a question in chatbot mode with conversation history support using RAG.
    
    Args:
        question: The user's question
        vectordb: The vector database
        llm: The language model to use
        conversation_history: List of (user_message, assistant_message) tuples
        pre_summary: Optional pre-generated summary
        company: Company name for context
    
    Returns:
        The assistant's response
    """
    if conversation_history is None:
        conversation_history = []
    
    # Build conversation history string
    history_str = ""
    if conversation_history:
        history_lines = []
        for user_msg, assistant_msg in conversation_history[-5:]:  # Last 5 exchanges for context
            history_lines.append(f"User: {user_msg}")
            history_lines.append(f"Assistant: {assistant_msg}")
        history_str = "\n".join(history_lines)
    else:
        history_str = "No previous conversation."
    
    # Retrieve relevant context from documents
    retriever = vectordb.as_retriever(search_type="mmr", search_kwargs={"k": 8})
    context_docs = retriever.invoke(question)
    context_blocks = []
    
    if pre_summary:
        context_blocks.append(f"Pre-summary (from summarizer):\n{pre_summary}\n")
    
    for d in context_docs:
        src = d.metadata.get("source", "unknown") if hasattr(d, "metadata") else "unknown"
        page = d.metadata.get("page", None) if hasattr(d, "metadata") else None
        page_note = f" (page {page})" if page is not None else ""
        context_blocks.append(f"Source: {src}{page_note}\n{d.page_content}\n")
    
    context = "\n---\n".join(context_blocks)
    
    # Use chatbot prompt with document context
    prompt = make_chatbot_prompt()
    chain = prompt | llm
    resp = chain.invoke({
        "context": context,
        "history": history_str,
        "question": question,
    })
    return getattr(resp, "content", str(resp))


def make_summary_prompt() -> Any:
    return ChatPromptTemplate.from_messages([
        (
            "system",
            (
                "You are a senior credit analyst. Summarize retrieved snippets into concise bullets "
                "with direct quotes and explicit citations including filename and page/row when present."
            ),
        ),
        (
            "human",
            (
                "Company: {company}\n\n"
                "Using only the context below, provide:\n"
                "- Top 8-12 key risk findings (bulleted), each with 1 short quote and citation.\n"
                "- A 4-6 sentence executive summary at the end.\n\n"
                "Context:\n{context}"
            ),
        ),
    ])


def summarize_corpus(company: str, vectordb: Any, summarizer_llm: Any) -> str:
    retriever = vectordb.as_retriever(search_type="mmr", search_kwargs={"k": 12})
    query = (
        f"Summarize materials for {company} focusing on credit risk, liquidity, leverage, compliance, operational risks, and outlook."
    )
    context_docs = retriever.invoke(query)
    context_blocks = []
    for d in context_docs:
        src = d.metadata.get("source", "unknown") if hasattr(d, "metadata") else "unknown"
        page = d.metadata.get("page", None) if hasattr(d, "metadata") else None
        page_note = f" (page {page})" if page is not None else ""
        context_blocks.append(f"Source: {src}{page_note}\n{d.page_content}\n")
    context = "\n---\n".join(context_blocks)

    prompt = make_summary_prompt()
    chain = prompt | summarizer_llm
    resp = chain.invoke({"company": company, "context": context})
    return getattr(resp, "content", str(resp))


def safe_json_loads(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = text[start : end + 1]
            try:
                return json.loads(snippet)
            except Exception:
                return {}
    return {}


def compute_safety_compliance_score(score_components: Dict[str, Any], fallback: Optional[float] = None) -> float:
    total = 0.0
    for key in SCORE_COMPONENT_KEYS:
        try:
            total += clamp(float(score_components.get(key, 0.0)), 0.0, 25.0)
        except (TypeError, ValueError):
            continue
    if total > 0.0:
        return clamp(total, 0.0, 100.0)
    if fallback is not None:
        try:
            return clamp(float(fallback), 0.0, 100.0)
        except (TypeError, ValueError):
            pass
    return 50.0


def format_fedex_review_output(parsed: Dict[str, Any], score: float, risk_band: str) -> str:
    sections = parsed.get("sections", {}) if isinstance(parsed, dict) else {}
    justifications = parsed.get("justifications", []) if isinstance(parsed, dict) else []
    trend = parsed.get("trend") if isinstance(parsed, dict) else None
    overall_summary = parsed.get("overall_summary") if isinstance(parsed, dict) else None
    document_breakdown = parsed.get("document_breakdown", {}) if isinstance(parsed, dict) else {}
    lines: List[str] = []
    lines.append("===== FedEx Safety & Compliance Review =====")
    lines.append("")
    if overall_summary:
        lines.append("Overall Summary:")
        lines.append(overall_summary.strip())
        lines.append("")
    for key in SECTION_DEFINITIONS.keys():
        meta = SECTION_DEFINITIONS[key]
        section = sections.get(key, {}) if isinstance(sections, dict) else {}
        summary = section.get("summary")
        if not summary:
            summary = "No summary returned for this section."
        lines.append(f"{meta['title']}:")
        lines.append(summary.strip())
        documents = section.get("documents") if isinstance(section, dict) else None
        if isinstance(documents, list) and documents:
            lines.append("Evidence by document:")
            for entry in documents:
                name = entry.get("name", "Unknown document")
                reason = entry.get("reason", "")
                lines.append(f"- {name}: {reason}")
        lines.append("")
    lines.append(f"Safety & Compliance Score: {score:.0f}/100")
    lines.append(f"Risk Band: {risk_band}")
    if trend:
        lines.append(f"Trend: {trend}")
    else:
        lines.append("Trend: Not stated")
    lines.append("")
    if justifications:
        lines.append("Evidence-Backed Justifications:")
        for bullet in justifications:
            lines.append(f"- {bullet}")
    else:
        lines.append("No justification bullets were returned.")
    if isinstance(document_breakdown, dict) and document_breakdown:
        lines.append("")
        lines.append("Document-Level Breakdown:")
        for doc_name, details in document_breakdown.items():
            role = ""
            impacts = []
            if isinstance(details, dict):
                role = details.get("role", "")
                impacts = details.get("impacts", [])
            lines.append(f"- {doc_name}: {role}")
            if impacts:
                lines.append(f"  Impacted areas: {', '.join(str(i) for i in impacts)}")
    return "\n".join(lines)


def fedex_review_analysis(
    company: str,
    vectordb: Any,
    llm: Any,
    pre_summary: str = "",
    criteria_weights: Optional[Dict[str, float]] = None,
) -> str:
    section_contexts = collect_section_context(vectordb)
    context_parts: List[str] = []
    if pre_summary:
        context_parts.append(f"Pre-summary:\n{pre_summary}")
    for key, meta in SECTION_DEFINITIONS.items():
        section_text = section_contexts.get(key, "")
        if not section_text:
            section_text = "No relevant context retrieved."
        context_parts.append(f"### {meta['title']}\n{section_text}")
    if criteria_weights:
        crit_lines = "\n".join([f"- {name}: weight {weight:.2f}" for name, weight in criteria_weights.items()])
        context_parts.append("### Decision Matrix Criteria\n" + crit_lines + "\nUse these names when citing impacts.")
    context_blob = "\n\n".join(context_parts)
    prompt = make_fedex_review_prompt()
    chain = prompt | llm
    resp = chain.invoke({"company": company, "context": context_blob})
    content = getattr(resp, "content", str(resp))
    parsed = safe_json_loads(content)
    score_components = parsed.get("score_components", {}) if isinstance(parsed, dict) else {}
    safety_score = compute_safety_compliance_score(score_components, parsed.get("safety_compliance_score") if isinstance(parsed, dict) else None)
    risk_band = parsed.get("risk_band") if isinstance(parsed, dict) else None
    if not isinstance(risk_band, str) or risk_band.strip() == "":
        risk_band = determine_risk_band(safety_score)
    result = format_fedex_review_output(parsed if isinstance(parsed, dict) else {}, safety_score, risk_band)
    return result


def parse_decision_matrix(xlsx_path: str) -> Dict[str, float]:
    if pd is None:
        raise RuntimeError("pandas is required to parse the decision matrix. Please install requirements.")
    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"Decision matrix not found: {xlsx_path}")
    
    try:
        df = pd.read_excel(xlsx_path, sheet_name=0)
        print(f"Decision matrix loaded. Shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        print(f"First few rows:\n{df.head()}")
        
        # Try to auto-detect columns
        name_cols = [c for c in df.columns if str(c).strip().lower() in {"criterion", "criteria", "category", "factor"}]
        weight_cols = [c for c in df.columns if str(c).strip().lower() in {"weight", "weights", "%", "percentage", "score weight"}]
        
        if not name_cols or not weight_cols:
            # Fallback: assume first text-like col is name, first numeric-like col is weight
            potential_name = None
            potential_weight = None
            for c in df.columns:
                series = df[c]
                if potential_name is None and series.dtype == object:
                    potential_name = c
                if potential_weight is None and pd.api.types.is_numeric_dtype(series):
                    potential_weight = c
            if potential_name and potential_weight:
                name_cols = [potential_name]
                weight_cols = [potential_weight]
                print(f"Using fallback columns - Name: {potential_name}, Weight: {potential_weight}")
        
        if not name_cols or not weight_cols:
            raise ValueError("Could not detect criterion and weight columns in the decision matrix.")
        
        names = df[name_cols[0]].astype(str).str.strip()
        weights = df[weight_cols[0]]
        
        # Drop rows with NaNs
        valid = weights.notna() & names.notna()
        names = names[valid]
        weights = weights[valid].astype(float)
        
        print(f"Valid rows: {len(names)}")
        print(f"Names: {names.tolist()}")
        print(f"Weights: {weights.tolist()}")
        
        # Normalize to 1.0
        total = float(weights.sum())
        print(f"Weight total: {total}")
        
        if total <= 0:
            raise ValueError(f"Decision matrix weights sum to zero or negative: {total}")
        
        norm_weights = (weights / total).tolist()
        crits = names.tolist()
        mapping: Dict[str, float] = {c: float(w) for c, w in zip(crits, norm_weights)}
        
        print(f"Normalized weights: {mapping}")
        return mapping
        
    except Exception as e:
        print(f"Error parsing decision matrix: {e}")
        print("Falling back to default criteria...")
        # Return default criteria if parsing fails
        return {
            "Financial Performance": 0.25,
            "Credit History": 0.20,
            "Industry Risk": 0.15,
            "Management Quality": 0.15,
            "Market Position": 0.10,
            "Operational Efficiency": 0.10,
            "Compliance": 0.05
        }


def compute_weighted_score(per_criterion_scores: Dict[str, float], weights: Dict[str, float]) -> float:
    score = 0.0
    for crit, weight in weights.items():
        val = float(per_criterion_scores.get(crit, 3.0))
        score += weight * val
    return score


def decide_from_score(weighted_score: float) -> Tuple[str, str]:
    # Thresholds: lower is lower risk
    if weighted_score >= 3.5:
        return "Decline", "High risk (>= 3.5)."
    if weighted_score >= 2.5:
        return "Conditional/Review", "Moderate risk (2.5–3.49). Consider covenants or collateral."
    return "Approve", "Low risk (< 2.5)."


def rag_weighted_analysis(company: str, vectordb: Any, prompt: Any, llm: Any, weights: Dict[str, float], pre_summary: str = "") -> str:
    retriever = vectordb.as_retriever(search_type="mmr", search_kwargs={"k": 12})
    query = (
        f"Analyze materials for {company}. Identify and score each criterion strictly using the context."
    )
    context_docs = retriever.invoke(query)
    context_blocks = []
    if pre_summary:
        context_blocks.append(f"Pre-summary (from summarizer):\n{pre_summary}\n")
    for d in context_docs:
        src = d.metadata.get("source", "unknown") if hasattr(d, "metadata") else "unknown"
        page = d.metadata.get("page", None) if hasattr(d, "metadata") else None
        page_note = f" (page {page})" if page is not None else ""
        context_blocks.append(f"Source: {src}{page_note}\n{d.page_content}\n")
    context = "\n---\n".join(context_blocks)

    # Ask model to assign scores per criterion with justifications in JSON
    criteria_list = "\n".join([f"- {c}: weight {w:.2f}" for c, w in weights.items()])
    scoring_task = (
        "You must provide JSON ONLY with keys: 'per_criterion', 'model_weighted_score', 'overall_recommendation', 'memo'. "
        "The 'per_criterion' value must be an object mapping criterion name to an object with 'score' (1-5) and 'justification' (string with citations). "
        "Compute 'model_weighted_score' as the weighted average based on the listed weights. "
        "Set 'overall_recommendation' to Approve, Conditional/Review, or Decline with a short rationale. "
        "Also include a concise 'memo' summarizing the analysis. "
        "Criteria and weights to use:\n" + criteria_list
    )

    chain = prompt | llm
    resp = chain.invoke({"context": context, "task": scoring_task})
    content = getattr(resp, "content", str(resp))

    # Try to parse JSON
    parsed: Dict[str, Any] = {}
    try:
        parsed = json.loads(content)
    except Exception:
        # If the LLM added prose, try to extract the first JSON object
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(content[start : end + 1])
            except Exception:
                parsed = {}

    per_criterion: Dict[str, Any] = parsed.get("per_criterion", {}) if isinstance(parsed, dict) else {}
    # Compute our own weighted score as a check
    simple_scores: Dict[str, float] = {}
    for crit in weights.keys():
        val = per_criterion.get(crit, {})
        try:
            simple_scores[crit] = float(val.get("score", 3.0))
        except Exception:
            simple_scores[crit] = 3.0
    weighted_score = compute_weighted_score(simple_scores, weights)
    decision, decision_reason = decide_from_score(weighted_score)

    # Build human-readable report
    lines: List[str] = []
    lines.append("Per-criterion scores:")
    for crit, w in weights.items():
        s = simple_scores.get(crit, 3.0)
        just = per_criterion.get(crit, {}).get("justification", "")
        lines.append(f"- {crit} (w={w:.2f}) -> {s:.2f}: {just}")
    lines.append("")
    lines.append(f"Weighted score (1=low risk, 5=high risk): {weighted_score:.2f}")
    lines.append(f"Computed recommendation: {decision} — {decision_reason}")
    if isinstance(parsed, dict):
        llm_score = parsed.get("model_weighted_score")
        llm_rec = parsed.get("overall_recommendation")
        memo = parsed.get("memo")
        if llm_score is not None:
            lines.append(f"Model-reported weighted score: {llm_score}")
        if llm_rec is not None:
            lines.append(f"Model recommendation: {llm_rec}")
        if memo:
            lines.append("")
            lines.append("Memo:")
            lines.append(str(memo))
    return "\n".join(lines)


def interactive_mode(vectordb: Any, prompt: Any, llm: Any) -> None:
    """Interactive Q&A mode for asking questions about the documents."""
    print("\n" + "="*60)
    print("INTERACTIVE DOCUMENT Q&A MODE")
    print("="*60)
    print("Ask questions about the documents. Type 'quit', 'exit', or 'q' to stop.")
    print("Type 'help' for example questions.\n")
    
    while True:
        try:
            question = input("Your question: ").strip()
            
            if question.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            elif question.lower() == 'help':
                print("\nExample questions:")
                print("- What are the main financial metrics mentioned?")
                print("- What risks are identified in the documents?")
                print("- What is the company's debt situation?")
                print("- What operational challenges are mentioned?")
                print("- Summarize the key findings from the SRS reports")
                print("- What compliance issues are discussed?")
                print()
                continue
            elif not question:
                continue
            
            print("\nSearching documents...")
            answer = rag_answer(question, vectordb, prompt, llm)
            print(f"\nAnswer:\n{answer}\n")
            print("-" * 60)
            
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}\n")


def main() -> None:
    # Load variables from .env at project root
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        # Fallback: try to load from environment variable or default location
        load_dotenv()
    print(f"OPENAI_API_KEY loaded: {bool(os.getenv('OPENAI_API_KEY'))}")

    parser = argparse.ArgumentParser(description="Risk rank businesses from documents using LangChain + OpenAI (with optional multi-LLM support)")
    parser.add_argument("--docs_dir", default=os.path.join(os.path.dirname(__file__), "files"), help="Directory of documents to analyze")
    parser.add_argument("--persist_dir", default=os.path.join(os.path.dirname(__file__), "chroma"), help="Chroma persistence directory")
    parser.add_argument("--model", default="gpt-4o-mini", help="[DEPRECATED] OpenAI chat model name (use --analyzer instead)")
    parser.add_argument("--summarizer", default=None, help="Summarizer LLM spec, e.g., 'openai:gpt-4o-mini' or 'ollama:llama3'")
    parser.add_argument("--analyzer", default=None, help="Analyzer LLM spec, e.g., 'openai:gpt-4o-mini' or 'ollama:llama3'")
    parser.add_argument("--company", default="Target Company", help="Company name for analysis")
    parser.add_argument("--decision_matrix", default=os.path.join(os.path.dirname(__file__), "files", "Decision Matrix's.xlsx"), help="Path to Excel decision matrix for scoring")
    parser.add_argument("--rebuild_index", action="store_true", help="Rebuild vector index from scratch")
    parser.add_argument("--interactive", action="store_true", help="Enter interactive Q&A mode")
    parser.add_argument("--question", help="Ask a single question about the documents")
    parser.add_argument("--mode", choices=["fedex", "weighted", "memo"], default="fedex", help="fedex: structured FedEx review (default), weighted: legacy decision matrix, memo: free-form memo")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to /Users/johngibson/Documents/College/Fall 2025/Ai and Agentic/.env or export it in your shell."
        )

    os.makedirs(args.persist_dir, exist_ok=True)

    doc_paths = discover_documents(args.docs_dir)
    if not doc_paths:
        raise RuntimeError(f"No supported documents found in {args.docs_dir}")

    # If rebuilding, clear any existing Chroma SQLite
    if args.rebuild_index and os.path.isdir(args.persist_dir):
        for f in os.listdir(args.persist_dir):
            try:
                os.remove(os.path.join(args.persist_dir, f))
            except Exception:
                pass

    docs = load_docs(doc_paths)
    vectordb = build_vectorstore(docs, args.persist_dir)

    # Build prompts and LLMs
    prompt = make_rag_prompt()
    analyzer_spec = args.analyzer or (f"openai:{args.model}" if args.model else None)
    analyzer_llm = make_llm_from_spec(analyzer_spec, default_openai_model="gpt-4o-mini")
    summarizer_llm = make_llm_from_spec(args.summarizer, default_openai_model="gpt-4o-mini") if args.summarizer else None

    # Handle different modes
    if args.interactive:
        interactive_mode(vectordb, prompt, analyzer_llm)
    elif args.question:
        print(f"\nQuestion: {args.question}")
        print("\nSearching documents...")
        pre_summary = summarize_corpus(args.company, vectordb, summarizer_llm) if summarizer_llm else ""
        answer = rag_answer(args.question, vectordb, prompt, analyzer_llm, pre_summary=pre_summary)
        print(f"\nAnswer:\n{answer}")
    else:
        pre_summary = summarize_corpus(args.company, vectordb, summarizer_llm) if summarizer_llm else ""
        decision_weights: Optional[Dict[str, float]] = None
        if args.mode in {"weighted", "fedex"} and args.decision_matrix and os.path.exists(args.decision_matrix):
            decision_weights = parse_decision_matrix(args.decision_matrix)
        if args.mode == "weighted":
            if decision_weights:
                answer = rag_weighted_analysis(args.company, vectordb, prompt, analyzer_llm, decision_weights, pre_summary=pre_summary)
            else:
                answer = "Decision matrix not found or could not be parsed. Provide --decision_matrix or switch --mode to fedex/memo."
        elif args.mode == "memo":
            user_query = (
                f"Analyze the provided materials for {args.company}. Identify credit risk factors, liquidity, profitability, leverage, compliance issues, operational risks, customer concentration, and industry outlook. Provide a 1-5 risk score and an approve/decline loan recommendation with rationale and citations."
            )
            answer = rag_answer(user_query, vectordb, prompt, analyzer_llm, pre_summary=pre_summary)
        else:
            answer = fedex_review_analysis(args.company, vectordb, analyzer_llm, pre_summary=pre_summary, criteria_weights=decision_weights)

        print("\n===== Risk Analysis =====\n")
        print(answer)


if __name__ == "__main__":
    main()
