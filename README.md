## TDF Risk Analysis (LangChain + OpenAI)

This tool ingests PDFs, images, and spreadsheets in `documents/`, builds a vector index, and generates a credit risk memo with score and approve/decline recommendation.

### Setup

1. Create a virtual environment (recommended) and install deps from project root:
   
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Add your OpenAI API key to a `.env` file at the project root (or export it):
   
   ```bash
   # .env (place in the project root)
   OPENAI_API_KEY=YOUR_KEY
   ```

   Or export directly:
   ```bash
   export OPENAI_API_KEY=YOUR_KEY
   ```

### Run

From the project root:

```bash
python TDF_risk_analysis/file.py \
  --docs_dir "TDF_risk_analysis/documents" \
  --persist_dir "TDF_risk_analysis/chroma" \
  --company "Target Company" \
  --model gpt-4o-mini \
  --rebuild_index
```

Flags:
- `--docs_dir`: directory containing PDFs, JPG/PNG, and XLSX/XLS files.
- `--persist_dir`: directory where Chroma DB persists.
- `--company`: name used in the analysis prompt.
- `--model`: OpenAI chat model (e.g., `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini`).
- `--rebuild_index`: clear and rebuild the vector index.

#### Decision Matrix Weighting

Provide your decision matrix Excel path via `--decision_matrix` (defaults to `documents/Decision Matrix's.xlsx`). The tool auto-detects the criterion and weight columns, normalizes weights, asks the model for per-criterion scores (1–5) with citations, and computes a weighted score and recommendation. If the matrix is missing, it falls back to a generic memo.

### Notes

- Supported types: `.pdf`, `.jpg`, `.jpeg`, `.png`, `.xlsx`, `.xls`.
- For images and complex PDFs, `unstructured` with OCR backends will be used if available.
- Output prints to terminal. Redirect to a file if desired:

  ```bash
  python TDF_risk_analysis/file.py > risk_report.txt
  ```


