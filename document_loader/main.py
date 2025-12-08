from dotenv import load_dotenv
from typing import TypedDict, Literal, List
from langgraph.graph import StateGraph, START, END
import os
from unstructured.partition.pdf import partition_pdf
from unstructured.staging.base import elements_to_json
from unstructured.partition.image import partition_image
from unstructured.partition.xlsx import partition_xlsx
from unstructured.documents.elements import Element
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from pydantic import BaseModel, Field
import asyncio
import base64
from langchain_core.messages import HumanMessage
import json
import traceback

load_dotenv()

class JSONFormat(BaseModel):
    json: str = Field(description="JSON onversion output from provided text")

llm = ChatOpenAI(model="gpt-5-nano", temperature=0)

table_agent_prompt = """
Role
You are an intelligent Data Parsing and JSON Structuring Agent. Your sole purpose is to analyze unstructured or semi-structured text input and convert it into strict, syntactically correct JSON.

Core Objective
Transform the user's input text into a structured JSON object or array. You must accurately identify keys (headers) versus values (body content), infer correct data types (strings, numbers, booleans), and handle irregular spacing or formatting logic.

Processing Rules
1. Analyze the Input
Scan for Structure: Determine if the text represents a collection of items (Array of Objects) or a single entity (Single Object).

Delimiter Detection: Identify separators (commas, tabs, pipes, varying whitespace) to split the data into logical units.

2. Key/Header Identification
Explicit Headers: If the first line appears to be a header row (e.g., "Name Age Role"), use these words as the JSON Keys. CamelCase or snake_case them automatically for cleaner syntax (e.g., "First Name" becomes firstName or first_name).

Implicit Keys: If no headers are present, inspect the data.

If it is a Key:Value list (e.g., "Color: Red"), use the left side as the key.

If it is raw data columns without headers, generate generic keys (e.g., field_1, field_2) or infer keys if context is obvious.

3. Data Structuring & Typing
Type Inference: unlike HTML, JSON is typed. You must infer the type:

Numbers: Convert "29" or "1,000.50" to raw numbers 29 or 1000.50. Do not wrap them in quotes unless they are identifiers like phone numbers.

Booleans: Convert "Yes/No", "True/False" to raw true or false.

Nulls: If a value is missing in a column-based structure, use null rather than an empty string.

Sanitization: Trim leading/trailing whitespace from strings. Remove newline characters (\n) from within value strings.

4. Format & Syntax
JSON Validity: The output must be valid JSON, parsing correctly in any standard linter.

Output Constraints: Output only the raw JSON inside a code block. Do not add conversational filler (e.g., "Here is your JSON").

Root Structure:

Use a root Array [...] if multiple rows/items exist.

Use a root Object {...} if only one complex entity is described.

Edge Case Handling
Vertical Key-Value Lists: If the input is a list (e.g., "Brand: Toyota \n Model: Camry"), convert this into a single flat JSON object.

Broken Lines: If a line of text seems to be a continuation of the previous data point (e.g., a multi-line description), merge it into the previous value string before closing the JSON property.

Irregular Spacing: If the input relies on visual spacing rather than strict delimiters, use semantic inference to group words into values (e.g., separating "New York" from "NY" even if spacing is inconsistent).
"""

table_agent = create_agent(llm,
                           name="JSON Structuring Agent",
                           system_prompt=table_agent_prompt,
                           response_format=JSONFormat)


image_description_agent_prompt="""
You are an expert Computer Vision Analyst and Data Archivist. Your specific role is to ingest images and generate highly detailed, semantic text descriptions optimized for vector embedding and retrieval in a RAG (Retrieval-Augmented Generation) system.

Your goal is to ensure that if a user searches for a specific data point, visual element, or text contained within the image, your description will allow the system to retrieve it accurately.

### Core Responsibilities

**1. General Image Processing:**
* **Subject Matter:** Identify the primary subjects, setting, and context.
* **OCR (Optical Character Recognition):** Transcribe all legible text visible in the image exactly as it appears.
* **Visual Elements:** Describe colors, styles (e.g., "photorealistic," "hand-drawn," "screenshot"), and spatial relationships.

**2. Graph & Chart Analysis (High Priority):**
If the image is a data visualization (bar chart, line graph, scatter plot, pie chart, etc.), you must extract the following specific details:
* **Chart Type:** Explicitly state the type of chart.
* **Title & Labels:** Transcribe the chart title, legend, X-axis label, and Y-axis label.
* **Data Range:** Note the minimum and maximum values displayed.
* **Trends:** Describe the visual trend (e.g., "positive correlation," "sharp decline in Q3," "steady growth").
* **Key Data Points:** Extract specific numerical values for peaks, troughs, and significant outliers.
* **Summary:** Provide a one-sentence synthesis of what the data proves or demonstrates.

### Formatting Constraints & Output Rules

* **NO NEWLINES:** The output must be a single, continuous string of text. You must strictly remove all newline characters (`\n`) and replace them with a single space.
* **Keyword Density:** Use synonyms where appropriate to broaden retrieval coverage (e.g., if you see a "canine," also mention "dog").
* **Objectivity:** Remain objective. Do not hallucinate data that is not visible. If a number is ambiguous, describe it as "approximately."

### Example Output Structure (Internal Logic)
(Note: The actual output must not have line breaks like this example)
[Image Type] [Title/Header Content] [Visual Description of components]

Example 1: Columnar Data
Input:

Plaintext

ID    Product Name       In Stock   Price
101   Wireless Mouse     Yes        25.99
102   Mechanical Keyboard No        120.00


Output:

JSON

[
  {
    "id": 101,
    "productName": "Wireless Mouse",
    "inStock": true,
    "price": 25.99
  },
  {
    "id": 102,
    "productName": "Mechanical Keyboard",
    "inStock": false,
    "price": 120.00
  }
]
"""

image_description_agent = create_agent(llm,
                                       name="Image Description Generator Agent",
                                       system_prompt=image_description_agent_prompt
                                       )


class State(TypedDict):
    file_path: str
    elements: List[Element]
    image_text: str
    chunks: dict
    embeddings: dict

# Nodes:
# - PDF Unstructured document extraction
# - XLSX Unstructured document extraction
# - Tables to HTML tables agent
# - Image description agent
# - (Not necessary for now) OCR correction agent
#   - determine bad elements somehow OR
#   - run all text through Claude (expensive?)
# - Clean unecessary information for elements
# - Embedding
# - Determine file type
# - Chunk
# - Find if needs to be processed or not

images = [".png", ".heic", ".jpg"]


def determine_file_type(state: State) -> Literal["pdf", "xlsx", "image", "other"]:
    path = state["file_path"]

    file_name, file_extension = os.path.splitext(path)
    file_extension = file_extension.lower()

    if file_extension == ".pdf":
        print("Determined file type as PDF...")
        return "pdf"
    elif file_extension == ".xlsx":
        print("Determined file type as XLSX...")
        return "xlsx"
    elif file_extension in images:
        print("Determined file type as image...")
        return "image"
    else:
        print("Unkown file type...")
        return "other"


def extract_pdf(state: State) -> State:
    print("Starting to extract text from PDF...")
    try:
        elements = partition_pdf(filename=state["file_path"], 
                                strategy="hi_res",
                                extract_images_in_pdf=True,
                                extract_image_block_output_dir="./images/")
        print("Successfully extracted text from PDF")
    except Exception as e:
        print("Unable to extract text!")
        print(e)
        
    return {"elements": elements}


def extract_xlsx(state: State) -> State:
    print("Starting to extract text from XLSX...")
    try:
        elements = partition_xlsx(filename=state["file_path"])
        print("Successfully extracted text from XLSX")
    except Exception as e:
        print("Unable to extract text!")
        print(e)

    return {"elements": elements}


def extract_image(state: State) -> State:
    elements = partition_image(filename=state["file_path"], strategy="hi_res")

    return {"elements": elements}

# ... existing imports ...

async def describe_extracted_images_async(state: State) -> State:
    elements = state["elements"]
    
    # Filter for Image elements that have a valid image path in metadata
    image_items = []
    for idx, el in enumerate(elements):
        if getattr(el, "category", None) == "Image":
            # Unstructured saves the path in metadata.image_path
            image_path = getattr(el.metadata, "image_path", None)
            if image_path and os.path.exists(image_path):
                image_items.append((idx, el, image_path))

    if not image_items:
        return {"elements": elements}

    print(f"Generating descriptions for {len(image_items)} extracted images...")

    # Prepare batch prompts
    prompts = []
    for _, _, path in image_items:
        b64_image = image_to_base64(path).decode('utf-8')
        message = HumanMessage(
            content=[
                {"type": "text", "text": "Describe this image for retrieval purposes:"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
            ]
        )
        prompts.append({"messages": [message]})

    # Run concurrently (adjust max_concurrency as needed)
    responses = await image_description_agent.abatch(prompts, config={"max_concurrency": 20})

    # Update elements with the generated description
    for (idx, el, _), resp in zip(image_items, responses):
        description = resp["messages"][-1].content
        # Update the element's text so it gets embedded later
        el.text = f"[Image Description: {description}]"
        elements[idx] = el

    for (idx, el, path), resp in zip(image_items, responses):
        description = resp["messages"][-1].content
        el.text = f"[Image Description: {description}]"
        elements[idx] = el
        
        # CLEANUP: Remove the image file after processing to save space
        try:
            os.remove(path)
        except OSError:
            pass # Handle edge cases where file is already gone

    return {"elements": elements}

def describe_extracted_images(state: State) -> State:
    return asyncio.run(describe_extracted_images_async(state))


def image_to_base64(filepath: str) -> bytes:
    """
    Read an image file and return a base64-encoded string.
    """
    with open(filepath, "rb") as f:
        img_bytes = f.read()
    b64_bytes = base64.b64encode(img_bytes)

    return b64_bytes


def describe_image(state: State) -> State:

    b64_image = image_to_base64(state["file_path"])
    image_string = b64_image.decode('utf-8')

    message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Describe this image: "
            },
            {
                "type": "image",
                "base64": image_string,
                "mime_type": "image/jpeg"
            }
        ]
    }

    result = image_description_agent.invoke({"messages": message})

    return {"image_text": result["messages"][-1].content}

def create_json(state: State) -> State:
    print("Starting process to create JSON...")
    elements = state["elements"]
    
    table_items = [(idx, el) for idx, el in enumerate(elements) if getattr(el, "category", None) == "Table"]
    
    if not table_items:
        return {"elements": elements}

    structured_llm = llm.with_structured_output(JSONFormat)

    responses = []

    for (i, (idx, el)) in enumerate(table_items):
        print(f"Working on table ({i + 1}/{len(table_items)})")
        
        raw_text = el.text.strip()
        lines = raw_text.split('\n')
        
        # --- CONFIGURATION ---
        # If text is huge (>5000 chars) but has few lines (<10), it's likely a "single line" dump.
        is_single_line_dump = len(lines) < 10 and len(raw_text) > 5000
        
        # If it's a single line dump, we process 1 "chunk" of characters at a time.
        # If it's normal lines, we process 50 lines at a time.
        BATCH_SIZE = 1 if is_single_line_dump else 50
        
        # --- PRE-PROCESSING ---
        if is_single_line_dump:
            print("  -> Detected single-line table structure. Switching to character-based chunking.")
            # Split raw text into chunks of 4000 characters (approx 1000 tokens)
            # This ensures we never send too much data, regardless of newlines.
            chunk_size = 4000
            # We create artificial "lines" where each line is actually a massive chunk
            lines = [raw_text[i:i+chunk_size] for i in range(0, len(raw_text), chunk_size)]
        
        # --- PROCESSING ---
        if len(lines) <= BATCH_SIZE:
            # Small table (or single small chunk)
            try:
                messages = [
                    {"role": "system", "content": table_agent_prompt},
                    {"role": "user", "content": f"Transform this text into JSON format: {raw_text}"}
                ]
                result = structured_llm.invoke(messages)
                responses.append(result.json)
            except Exception as e:
                print(f"Error converting table {i+1}: {e}")
                responses.append("[]")
        
        else:
            # Large table - Process in Batches
            print(f"  -> Processing in batches ({len(lines)} chunks/rows)...")
            
            all_rows_data = []
            detected_keys = []
            
            # 1. Process First Chunk to get Schema
            first_chunk_text = "\n".join(lines[:BATCH_SIZE])
            try:
                # We add specific instruction to handle cut-off data if we are in char-chunk mode
                prompt_suffix = "If the data cuts off in the middle of a row at the end, ignore that partial row." if is_single_line_dump else ""
                
                messages = [
                    {"role": "system", "content": table_agent_prompt},
                    {"role": "user", "content": f"Transform this text into JSON. Identify the correct headers/keys. {prompt_suffix}\n\nData:\n{first_chunk_text}"}
                ]
                result_first = structured_llm.invoke({"messages": messages})
                
                json_data = json.loads(result_first.json)
                if isinstance(json_data, dict): json_data = [json_data]
                
                all_rows_data.extend(json_data)

                if json_data and isinstance(json_data[0], dict):
                    detected_keys = list(json_data[0].keys())
                    print(f"  -> Schema detected: {detected_keys}")
            
            except Exception as e:
                print(f"CRITICAL: Failed to process first chunk. Error: {e}")
                responses.append("[]")
                continue

            # 2. Process Remaining Chunks
            for j in range(BATCH_SIZE, len(lines), BATCH_SIZE):
                chunk_lines = lines[j : j + BATCH_SIZE]
                chunk_text = "\n".join(chunk_lines)
                
                context_prompt = (
                    f"Continue processing this table. "
                    f"Use these JSON keys strictly: {detected_keys}. "
                    f"If the text starts or ends with a broken/partial word or number, ignore that partial entity. "
                    f"Raw Text:\n{chunk_text}"
                )

                try:
                    messages = [
                        {"role": "system", "content": table_agent_prompt},
                        {"role": "user", "content": context_prompt}
                    ]
                    result_chunk = structured_llm.invoke({"messages": messages})
                    
                    parsed_chunk = json.loads(result_chunk.json)
                    if isinstance(parsed_chunk, list):
                        all_rows_data.extend(parsed_chunk)
                    elif isinstance(parsed_chunk, dict):
                        all_rows_data.append(parsed_chunk)
                        
                except Exception as e:
                    print(f"Error converting batch at index {j}: {e}")

            responses.append(json.dumps(all_rows_data))

    print("Processed data into JSON...")

    for (idx, el), resp in zip(table_items, responses):
        el.text = resp

    return {"elements": elements}


def remove_unecessary_metadata() -> State:
    return None


def chunk() -> State:
    return None


def embed() -> State:
    return None

# 1. Extract text with Unstructured
# 2. Create image descriptions with OpenAI
# 3. (Optional) Use Claude OCR for cleanup
# 4. Create HTML tables for tables

builder = StateGraph(State)

builder.add_node("extract_pdf", extract_pdf)
builder.add_node("extract_xlsx", extract_xlsx)
builder.add_node("extract_image", extract_image)
builder.add_node("describe_image", describe_image)
builder.add_node("create_json", create_json)
builder.add_node("describe_extracted_images", describe_extracted_images)

builder.add_conditional_edges(START, 
                                determine_file_type, 
                                {
                                  "pdf": "extract_pdf", 
                                  "xlsx": "extract_xlsx",
                                  "image": "extract_image",
                                   "other": END
                                }
                            )

builder.add_edge("extract_pdf", "describe_extracted_images")
builder.add_edge("extract_xlsx", "describe_extracted_images")

# After describing images, we can move to table creation
builder.add_edge("describe_extracted_images", "create_json")

builder.add_edge("extract_image", "create_json")

builder.add_edge("create_json", END)


workflow = builder.compile()

script_dir = os.path.dirname(os.path.abspath(__file__))

# 2. Join it with your target folder name
# This assumes the "files" folder is inside the "document_loader" folder alongside main.py
input_directory = os.path.join(script_dir, "..", "files")

output = []

# Verify the directory exists
if os.path.exists(input_directory):
    # Iterate over every entry in the directory
    for filename in os.listdir(input_directory):
        file_path = os.path.join(input_directory, filename)
        
        # Ensure we are processing a file, not a subdirectory
        if os.path.isfile(file_path):
            print(f"\n--- Processing: {filename} ---")
            
            try:
                # Invoke the workflow for the current file path
                state = workflow.invoke({"file_path": file_path})
                
                # Check if elements were successfully extracted
                if "elements" in state:
                    elements = state["elements"]
                    print(f"Successfully processed {filename}. Extracted {len(elements)} elements.")
                    
                    # Convert elements to dictionaries so they are JSON serializable
                    elements_dicts = [el.to_dict() for el in elements]
                    output.append(elements_dicts)
                else:
                    # Handle cases where the file type was 'other' or skipped
                    print(f"Skipped {filename}: No elements extracted or unsupported file type.")
                    
            except Exception as e:
                print(f"Error processing {filename}: {e}")
                traceback.print_exc()
else:
    print(f"Directory '{input_directory}' not found. Please create it and add files.")



# Save to a specific output file, e.g., 'output.json'
output_file_path = os.path.join(script_dir, "output.json")

with open(output_file_path, 'w', encoding='utf-8') as f:
        # json.dump writes the dictionary directly to the file
        # indent=4 makes it readable (pretty-printed)
        # ensure_ascii=False allows special characters (like emojis or accents) to be saved correctly
        json.dump(output, f, indent=4, ensure_ascii=False)
    
print(f"Successfully saved nested dictionary to {filename}")