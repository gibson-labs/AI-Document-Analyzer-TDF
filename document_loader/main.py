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

llm = ChatOpenAI(model="gpt-5-nano", temperature=0)

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