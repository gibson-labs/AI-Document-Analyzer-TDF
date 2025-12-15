import argparse
import base64
import io
import operator
import os
from pathlib import Path
from typing import Annotated, Dict, List, NotRequired, TypedDict
from pdf2image import convert_from_path
from PIL import Image
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END, START
from langgraph.constants import Send
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
MODEL_NAME = "gpt-5-mini"
VALID_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
VISION_OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_text"

# --- 1. Data Models (Schema) ---

class PageVisualAnalysis(BaseModel):
    """Structured output focusing ONLY on visual elements."""
    has_relevant_visuals: bool = Field(..., description="True if the page contains a chart, graph, diagram, or photograph. False if it is text/tables only.")
    visual_description: str = Field(..., description="Detailed description of the visual elements. Ignore all body text and standard data tables.")
    visual_types: List[str] = Field(..., description="List of types found, e.g., ['bar_chart', 'scatter_plot', 'network_diagram', 'photograph'].")
    confidence_score: float = Field(..., description="Confidence in the interpretation of the visual data.")

class AnalysisTask(TypedDict):
    """Payload for the worker node."""
    filename: str
    page_index: int # 1-based index
    image_data: str # Base64 string

class AgentState(TypedDict):
    """Global state of the graph."""
    # Input
    folder_path: str
    output_dir: NotRequired[Path]

    # Internal
    tasks: List[AnalysisTask]

    # Output (Reducer)
    results: Annotated[List[dict], operator.add]
    grouped_results: NotRequired[Dict[str, List[dict]]]
    written_files: NotRequired[List[str]]

# --- 2. Helper Functions ---

def encode_pil_image(image_obj):
    """Converts a PIL image to a base64 string."""
    buffered = io.BytesIO()
    # Convert to RGB to handle PNG transparency issues if necessary
    if image_obj.mode in ("RGBA", "P"):
        image_obj = image_obj.convert("RGB")
    image_obj.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def write_grouped_results(grouped_results: Dict[str, List[dict]], output_dir: Path = VISION_OUTPUT_DIR) -> List[str]:
    """Persist grouped vision summaries to per-file text outputs."""
    if not grouped_results:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths = []

    for fname, entries in grouped_results.items():
        lines = []
        for entry in entries:
            page_label = f"Page {entry.get('page')}" if entry.get("page") is not None else "Page"
            lines.append(page_label)

            description = entry.get("description", "")
            if description:
                lines.append(description)

            visual_types = entry.get("visual_types") or []
            if visual_types:
                lines.append(f"Visual types: {', '.join(visual_types)}")

            confidence = entry.get("confidence")
            if confidence is not None:
                lines.append(f"Confidence: {confidence}")

            lines.append("")  # spacer between pages

        output_path = output_dir / f"{Path(fname).stem}-vision.txt"
        output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        written_paths.append(str(output_path))

    return written_paths

# --- 3. Nodes ---

def folder_loader_node(state: AgentState):
    """
    Scans a folder, processes PDFs/Images, and creates a task list.
    """
    folder = state['folder_path']
    print(f"--- Scanning Folder: {folder} ---")
    
    tasks = []
    
    if not os.path.exists(folder):
        raise FileNotFoundError(f"Folder not found: {folder}")

    for root, _, files in os.walk(folder):
        for file in files:
            file_path = os.path.join(root, file)
            _, ext = os.path.splitext(file)
            
            if ext.lower() not in VALID_EXTENSIONS:
                continue
            
            print(f"Processing: {file}")
            
            # Case 1: PDF
            if ext.lower() == ".pdf":
                try:
                    # dpi=300 is crucial for reading chart axis labels
                    pil_images = convert_from_path(file_path, dpi=300)
                    for i, img in enumerate(pil_images):
                        tasks.append({
                            "filename": file,
                            "page_index": i + 1,
                            "image_data": encode_pil_image(img)
                        })
                except Exception as e:
                    print(f"Failed to process PDF {file}: {e}")

            # Case 2: Image
            else:
                try:
                    with Image.open(file_path) as img:
                        tasks.append({
                            "filename": file,
                            "page_index": 1,
                            "image_data": encode_pil_image(img)
                        })
                except Exception as e:
                    print(f"Failed to process Image {file}: {e}")

    print(f"Total pages/images to analyze: {len(tasks)}")
    return {"tasks": tasks, "results": []}

def vision_analyzer_node(state: AnalysisTask):
    """
    Worker Node: Analyzes visuals only.
    """
    filename = state['filename']
    page_idx = state['page_index']
    
    # print(f"--- Analyzing {filename} (Page {page_idx}) ---")
    
    llm = ChatOpenAI(model=MODEL_NAME, temperature=0)
    structured_llm = llm.with_structured_output(PageVisualAnalysis)
    
    # STRICT Prompt to ignore text
    system_prompt = (
        "You are a Business Diagram Analysis Agent. "
        "Your GOAL: Interpret charts, graphs, diagrams, and photos for a vector database. "
        "Don't focus too much on how it looks, but rather the interpretation of what each diagram means."
        "RESTRICTION: IGNORE all body text, headlines, and standard data tables. "
        "If the image only contains text or simple tables, set 'has_relevant_visuals' to False."
    )
    
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Analyze the visuals in this image."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{state['image_data']}"},
            },
        ]
    )
    
    try:
        response: PageVisualAnalysis = structured_llm.invoke([SystemMessage(content=system_prompt), msg])
        
        # Filter out empty results to save DB space
        if not response.has_relevant_visuals:
            return {"results": []}
            
        return {
            "results": [{
                "filename": filename,
                "page": page_idx,
                "description": response.visual_description,
                "visual_types": response.visual_types,
                "confidence": response.confidence_score
            }]
        }
    except Exception as e:
        print(f"Error analyzing {filename}: {e}")
        return {"results": []}

def aggregator_node(state: AgentState):
    """Group results by document and persist them to per-file text outputs."""
    print("--- Aggregating Results ---")
    raw_results = state['results']

    # Group by filename for cleaner output
    grouped = {}
    for item in raw_results:
        fname = item['filename']
        if fname not in grouped:
            grouped[fname] = []
        grouped[fname].append(item)

    # Sort pages within files
    for fname in grouped:
        grouped[fname].sort(key=lambda x: x['page'])

    # Use custom output directory if provided in state
    output_dir = state.get('output_dir', VISION_OUTPUT_DIR)
    written_files = write_grouped_results(grouped, output_dir)
    return {"grouped_results": grouped, "written_files": written_files}

# --- 4. Edge Logic ---

def map_tasks(state: AgentState):
    """
    Distributes tasks to parallel workers.
    """
    return [
        Send("vision_analyzer", task) 
        for task in state['tasks']
    ]

# --- 5. Graph Definition ---

workflow = StateGraph(AgentState)

workflow.add_node("folder_loader", folder_loader_node)
workflow.add_node("vision_analyzer", vision_analyzer_node)
workflow.add_node("aggregator", aggregator_node)

workflow.add_edge(START, "folder_loader")

workflow.add_conditional_edges(
    "folder_loader",
    map_tasks,
    path_map=["vision_analyzer"]
)

workflow.add_edge("vision_analyzer", "aggregator")
workflow.add_edge("aggregator", END)

app = workflow.compile()

# --- 6. Execution ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract and analyze visual elements (charts, graphs, diagrams) from PDFs and images."
    )
    parser.add_argument(
        "target_folder",
        nargs="?",
        default="./files",
        help="Path to the folder containing PDF/image files to process (default: ./files)"
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Directory to save extracted vision summaries (default: {VISION_OUTPUT_DIR})"
    )

    args = parser.parse_args()
    target_folder = args.target_folder

    if os.path.exists(target_folder):
        # Prepare initial state
        initial_state = {"folder_path": target_folder}
        if args.output_dir:
            initial_state["output_dir"] = Path(args.output_dir)

        final_state = app.invoke(initial_state)

        # Display Results
        import json
        output = final_state.get("grouped_results", final_state.get("results", {}))
        print(json.dumps(output, indent=2))

        written_files = final_state.get("written_files") or []
        if written_files:
            print("\nSaved vision summaries:")
            for path in written_files:
                print(f" - {path}")
    else:
        print(f"Error: Folder '{target_folder}' does not exist.")
        print(f"Please create the folder and add some PDFs/Images, or specify a different path.")
