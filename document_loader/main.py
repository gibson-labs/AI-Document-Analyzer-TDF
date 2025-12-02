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

load_dotenv()

class HTMLTable(BaseModel):
    html_table: str = Field(description="A raw HTML table rendering of the text with no formatting.")

llm = ChatOpenAI(model="gpt-5-mini", temperature=0)

table_agent_prompt = """
# Role
You are an intelligent Data Parsing and HTML Structuring Agent. Your sole purpose is to analyze unstructured or semi-structured text input and convert it into clean, semantically correct HTML tables.

# Core Objective
Transform the user's input text into a `<table>` structure, accurately identifying headers versus body content, and inferring column alignment even when the source text has irregular spacing or formatting.

# Processing Rules

1.  **Analyze the Input:**
    * Scan the text to identify potential delimiters (commas, tabs, pipes, multiple spaces).
    * Determine the likely number of columns based on line density and repeating patterns.

2.  **Header Identification (Crucial):**
    * Assume the first non-empty line contains the headers unless the data clearly suggests otherwise (e.g., Key/Value pairs).
    * If the first line looks distinct (all caps, different naming convention) or summarizes the data below, treat it as the Header Row.
    * Wrap header cells in `<thead>` and `<tr>` using `<th>` tags. Scope them appropriately (e.g., `scope="col"`).

3.  **Body Structuring:**
    * Wrap the actual data in `<tbody>`.
    * Use `<tr>` for rows and `<td>` for data cells.
    * **Missing Data:** If a row has fewer data points than the header, pad the remaining cells with `&nbsp;` or empty `<td></td>` to maintain table structural integrity.
    * **Merging:** If a line seems to be a continuation of the previous row (e.g., a wrapped address), merge it logically rather than creating a new broken row.

4.  **Format & Syntax:**
    * **Character Sanitization:** Explicitly remove any newline characters (`\n`) or line breaks from within the cell content. The text inside a `<td>` or `<th>` should be a single continuous string.
    * Output **only** the raw HTML code inside a code block. Do not add CSS or styling attributes (like `border="1"`) inline.
    * Do not include markdown conversational filler (e.g., "Here is your table"). Just the code.
    * Include a `<caption>` tag only if the user explicitly provides a title or the text clearly has a standalone title above the data.

# Edge Case Handling
* **Lists:** If the input is a vertical list of Key: Value pairs, convert this into a two-column table (Header: "Attribute", "Value").
* **Irregular Spacing:** If the input uses spaces to separate columns visually but they aren't aligned perfectly, use semantic inference to group words into cells.

# Example 1
**Input:**
Name    Age  Occupation
John Doe 29 Engineer
Jane \n Smith    34   Doctor

**Output:**
<table>
  <thead>
    <tr>
      <th scope="col">Name</th>
      <th scope="col">Age</th>
      <th scope="col">Occupation</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>John Doe</td>
      <td>29</td>
      <td>Engineer</td>
    </tr>
    <tr>
      <td>Jane Smith</td>
      <td>34</td>
      <td>Doctor</td>
    </tr>
  </tbody>
</table>
"""

table_agent = create_agent(llm,
                           name="HTML Table Creation Agent",
                           system_prompt=table_agent_prompt,
                           response_format=HTMLTable)

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
"""

image_description_agent = create_agent(llm,
                                       name="Image Description Generator Agent",
                                       system_prompt=image_description_agent_prompt
                                       )


class State(TypedDict):
    file_path: str
    elements: List[Element]
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

def image_to_base64(filepath: str) -> bytes:
    """
    Read an image file and return a base64-encoded string.
    """
    with open(filepath, "rb") as f:
        img_bytes = f.read()
    b64_bytes = base64.b64encode(img_bytes)
    # Convert bytes -> utf-8 string
    return b64_bytes

def describe_image(state: State) -> State:

    b64_image = image_to_base64(state["file_path"])
    image_string = b64_image.decode('utf-8')

    print(image_string)

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

    return {"output": result["messages"][-1].content}

def extract_image(state: State) -> State:
    elements = partition_image(filename=state["file_path"], strategy="hi_res")

    return {"elements": elements}


async def create_html_table_async(state: State) -> State:
    elements = state["elements"]
    table_items = [(idx, el) for idx, el in enumerate(elements) if getattr(el, "category", None) == "Table"]
    if not table_items:
        return {"elements": elements}

    prompts = [
        {"messages": [{"role": "user", "content": f"Transform this text into an html table: {el.text}"}]}
        for _, el in table_items
    ]

    # Runs calls concurrently; tune max_concurrency to stay under rate limits
    responses = await table_agent.abatch(prompts, config={"max_concurrency": 5})

    for (idx, el), resp in zip(table_items, responses):
        el.text = resp["structured_response"].html_table
        elements[idx] = el

    return {"elements": elements}

def create_html_table(state: State) -> State:
    return asyncio.run(create_html_table_async(state))


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
builder.add_node("create_html_table", create_html_table)
# builder.add_node("remove_unecessary_metadata", remove_unecessary_metadata)
# builder.add_node("chunk", chunk)
# builder.add_node("embed", embed)

builder.add_conditional_edges(START, 
                                determine_file_type, 
                                {
                                  "pdf": "extract_pdf", 
                                  "xlsx": "extract_xlsx",
                                  "image": "extract_image",
                                   "other": END
                                }
                            )

builder.add_edge("extract_pdf", "create_html_table")
builder.add_edge("extract_xlsx", "create_html_table")
builder.add_edge("extract_image", "create_html_table")

builder.add_edge("create_html_table", END)

# builder.add_edge("describe_image", END)


# builder.add_edge("create_html_table", "describe_image")
# builder.add_edge("describe_image", "remove_unecessary_metadata")
# builder.add_edge("remove_unecessary_metadata", "chunk")
# builder.add_edge("chunk", "embed")
# builder.add_edge("embed", END)

workflow = builder.compile()

state: State = workflow.invoke({"file_path": "./files/Schedule A.pdf"})
elements = state["elements"]

for element in elements:
    print(element.__dict__)