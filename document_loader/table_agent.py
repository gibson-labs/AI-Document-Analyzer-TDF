import os
import boto3
import sqlite3
import pandas as pd
import time
import io
from typing import Literal
from pypdf import PdfReader, PdfWriter  # Added for splitting PDFs

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
DB_PATH = "extracted_data.db"
AWS_REGION = "us-west-2"
AWS_PROFILE = "806817393652_AdministratorAccess"  # Update this or use environment variables

# --- System Prompt ---
SYSTEM_PROMPT = """You are an expert SQL Data Architect specialized in Entity Resolution and Schema Normalization.
Your goal is to build a SINGLE, COHERENT relational database from a messy folder of disparate documents.

CRITICAL MENTALITY:
The documents are pieces of a larger puzzle. Data in one file (e.g., a list of addresses) likely relates to data in another (e.g., a list of names).
You must infer these relationships.

CORE PROTOCOL:
1. **Analyze & Extract**: When given a file, extract the table data.
2. **Schema Reconnaissance (MANDATORY)**: Before doing ANYTHING with SQL, run `get_db_schema`.
3. **Entity Matching**:
   - Look at the existing tables. Does the new data belong to an existing entity?
   - Example: If you have a `customers` table and find a new file with "Client Name" and "Phone", MAP IT to `customers`. Do NOT create `clients_table`.
   - If column names differ (e.g., "Cost" vs "Price"), standardize them to the existing schema.
4. **Data Integration**:
   - If the table exists: INSERT the data. If the new file has *new* fields that describe the entity, you may ALTER the table to add columns, or create a linked table (Foreign Key) if it's a one-to-many relationship.
   - If the table does not exist: Create a new generic table (e.g., `orders`, `inventory`). NEVER include the filename in the table name.
5. **Sanitization**:
   - Convert all column headers to snake_case.
   - Ensure Primary Keys are consistent.

RULES FOR MESSY DATA:
- If a file contains multiple disparate tables, split them into appropriate entities.
- If you find a "Foreign Key" (e.g., an Order ID in a shipping log), ensure you link it effectively to the main Orders table if it exists.
- Do not duplicate data.
"""

# --- Tools ---

def _parse_textract_response(response) -> str:
    """Helper to parse raw Textract JSON into a Markdown formatted string."""
    blocks = response['Blocks']
    blocks_map = {block['Id']: block for block in blocks}
    tables = []
    
    for block in blocks:
        if block['BlockType'] == 'TABLE':
            rows = {}
            for relationship in block.get('Relationships', []):
                if relationship['Type'] == 'CHILD':
                    for child_id in relationship['Ids']:
                        cell = blocks_map[child_id]
                        if cell['BlockType'] == 'CELL':
                            row_index = cell['RowIndex']
                            col_index = cell['ColumnIndex']
                            text = ""
                            if 'Relationships' in cell:
                                for cell_rel in cell['Relationships']:
                                    if cell_rel['Type'] == 'CHILD':
                                        for word_id in cell_rel['Ids']:
                                            word = blocks_map[word_id]
                                            if word['BlockType'] == 'WORD':
                                                text += word['Text'] + " "
                                            elif word['BlockType'] == 'SELECTION_ELEMENT' and word['SelectionStatus'] == 'SELECTED':
                                                text += "[X] "
                            if row_index not in rows:
                                rows[row_index] = {}
                            rows[row_index][col_index] = text.strip()
            
            sorted_rows = sorted(rows.items())
            if not sorted_rows:
                continue
            md_table = ""
            max_col = max(max(r.keys()) for _, r in rows.items())
            for r_idx, row_data in sorted_rows:
                row_str = "|"
                for c in range(1, max_col + 1):
                    val = row_data.get(c, "")
                    row_str += f" {val} |"
                md_table += row_str + "\n"
            tables.append(md_table)

    return "\n\n".join(tables) if tables else "No tables found in Textract response."

@tool
def process_document_with_textract(filepath: str):
    """
    Use this tool for PDFs, PNGs, JPGs, or TIFFs. 
    It sends the file to AWS Textract and returns a Markdown representation of any tables found.
    Handles multi-page PDFs by processing one page at a time.
    """
    try:
        session = boto3.Session(profile_name=AWS_PROFILE)
        client = session.client('textract', region_name=AWS_REGION)
        
        extracted_content = []
        
        # --- PDF Handling (Split Pages) ---
        if filepath.lower().endswith('.pdf'):
            try:
                reader = PdfReader(filepath)
                num_pages = len(reader.pages)
                print(f"Detected {num_pages} pages in PDF: {filepath}")
                
                for i, page in enumerate(reader.pages):
                    writer = PdfWriter()
                    writer.add_page(page)
                    
                    # Write single page to bytes
                    with io.BytesIO() as output_stream:
                        writer.write(output_stream)
                        page_bytes = output_stream.getvalue()
                    
                    print(f"  - Analyzing page {i+1}/{num_pages}...")
                    
                    # AnalyzeDocument works on single-page PDF bytes
                    response = client.analyze_document(
                        Document={'Bytes': page_bytes}, 
                        FeatureTypes=['TABLES']
                    )
                    
                    page_md = _parse_textract_response(response)
                    extracted_content.append(f"### Page {i+1}\n{page_md}")
            
            except Exception as pdf_error:
                return f"Error reading PDF structure (ensure pypdf is installed): {str(pdf_error)}"

        # --- Image Handling (Single file) ---
        else:
            with open(filepath, 'rb') as document:
                image_bytes = document.read()
                
            response = client.analyze_document(
                Document={'Bytes': image_bytes}, 
                FeatureTypes=['TABLES']
            )
            extracted_content.append(_parse_textract_response(response))
            
        return "\n\n".join(extracted_content)
        
    except Exception as e:
        return f"Error processing file with Textract: {str(e)}"
    
# @tool
# def query_textract(query: str):
#     return None

@tool
def process_spreadsheet(filepath: str):
    """Use this tool for Excel (.xlsx, .xls) or CSV files."""
    try:
        if filepath.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)
        return df.head(50).to_markdown(index=False)
    except Exception as e:
        return f"Error reading spreadsheet: {str(e)}"

@tool
def execute_sqlite_query(query: str):
    """Executes a SQL query against the 'extracted_data.db' SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Enable running multiple statements (e.g. CREATE then INSERT) to save steps
        if ";" in query:
             cursor.executescript(query)
             conn.commit()
             conn.close()
             return "Multiple queries executed successfully."
        
        cursor.execute(query)
        if query.strip().upper().startswith("SELECT"):
            results = cursor.fetchall()
            conn.close()
            return str(results)
        else:
            conn.commit()
            conn.close()
            return "Query executed successfully."
    except Exception as e:
        return f"SQL Error: {str(e)}"

@tool
def get_db_schema():
    """Returns the list of current tables and their schemas in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    conn.close()
    return str(tables)

# --- Graph / Agent Construction ---

tools = [
    process_document_with_textract,
    process_spreadsheet,
    execute_sqlite_query,
    get_db_schema
]

# 1. Bind tools to the model
# Switch to gpt-4o-mini for better rate limits and cost efficiency
# Added max_retries=5 to handle 429 errors automatically
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_retries=5)
model_with_tools = llm.bind_tools(tools)

# 2. Define the Nodes
def call_model(state: MessagesState):
    """Node that calls the LLM."""
    messages = state["messages"]
    response = model_with_tools.invoke(messages)
    return {"messages": [response]}

def should_continue(state: MessagesState) -> Literal["tools", END]:
    """Conditional logic to decide next step."""
    messages = state["messages"]
    last_message = messages[-1]
    
    if last_message.tool_calls:
        return "tools"
    return END

# 3. Build the Graph
workflow = StateGraph(MessagesState)

workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(tools))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue)
workflow.add_edge("tools", "agent")

checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer)

with open("workflow_graph.png", "wb") as f:
    f.write(graph.get_graph(xray=True).draw_mermaid_png())

# --- Invocation Logic ---

def process_folder(folder_path: str):
    if not os.path.exists(folder_path):
        print(f"Folder {folder_path} does not exist.")
        return

    print(f"--- Starting Ingestion for Folder: {folder_path} ---")
    
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    
    # SORTING: We sort the files to ensure deterministic processing order (e.g., File1, File2)
    # This helps if you have numbered files where order matters for relationships.
    files.sort()
    
    # GLOBAL THREAD ID:
    # We use a single constant thread_id ("global_ingestion_job") for ALL files.
    # This ensures the Agent shares memory across files (e.g., "I just created the Customers table, so I'll use it again").
    config = {
        "configurable": {"thread_id": "global_ingestion_job"}, 
        "recursion_limit": 50
    }

    # Initialize with System Prompt ONCE at the start of the job
    # We invoke with just the system prompt first to set the persona
    # (Optional, but helps ground the agent before the first file)
    initial_state = {"messages": [SystemMessage(content=SYSTEM_PROMPT)]}
    
    # We don't need to run the graph just for the system prompt, we can just prepend it
    # to the first file's interaction or let it persist in memory if we used `graph.update_state`.
    # For simplicity, we will pass the system prompt with every new file request, 
    # but since we share the thread_id, the history will stack up.
    
    for i, filename in enumerate(files):
        full_path = os.path.join(folder_path, filename)
        print(f"\nProcessing File {i+1}/{len(files)}: {filename}...")
        
        # We only send the System Prompt explicitly on the FIRST iteration if we want to be clean,
        # but sending it every time reinforces the rules. 
        # Given we want strong schema coherence, we'll remind it every time.
        
        message_content = f"Step {i+1}: Process the tables in this file: {full_path}. Check the EXISTING schema first."
        
        # If it's the very first file, include the System Prompt.
        # For subsequent files, the history (and system prompt) is already in the thread memory.
        if i == 0:
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=message_content)
            ]
        else:
            messages = [HumanMessage(content=message_content)]
        
        # Run the graph
        for event in graph.stream({"messages": messages}, config, stream_mode="values"):
            event["messages"][-1].pretty_print()
            
        print("Pausing for 2 seconds to respect rate limits...")
        time.sleep(2)
            
    print("\n--- Ingestion Complete ---")

if __name__ == "__main__":
    target_folder = "../files"

    if not os.path.exists(target_folder):
        os.makedirs(target_folder)
        print(f"Created placeholder folder at {target_folder}. Please add files there.")
    else:
        process_folder(target_folder)