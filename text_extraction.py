from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None  # type: ignore
    BotoCoreError = ClientError = Exception  # type: ignore

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None  # type: ignore

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

SUPPORTED_FILE_EXTS = {".pdf", ".xlsx", ".png", ".jpg", ".jpeg"}
DEFAULT_FILES_DIR = Path(__file__).resolve().parent / "files"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_text"


def _build_textract_client():
    """Initialize a Textract client using env credentials or AWS profile."""
    if not boto3:
        return None

    session_kwargs = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        session_kwargs.update(
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )

    try:
        session = boto3.Session(**session_kwargs)
        creds = session.get_credentials()
        if creds is None:
            return None
        return session.client("textract")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Failed to initialize Textract client: {exc}")
        return None


# Lazily initialize Textract so environments without AWS creds do not crash on import.
textract_client = _build_textract_client()


def extract_with_textract(file_bytes: bytes, source_name: str) -> str:
    """Call Textract detect_document_text on provided bytes and return concatenated lines."""
    if not textract_client:
        raise RuntimeError("AWS Textract is not configured (check AWS credentials/region).")

    try:
        response = textract_client.detect_document_text(Document={"Bytes": file_bytes})
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Textract call failed for {source_name}: {exc}") from exc

    lines: List[str] = []
    for block in response.get("Blocks", []):
        if block.get("BlockType") == "LINE" and block.get("Text"):
            lines.append(block["Text"])
    return "\n".join(lines)


def extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF using Textract on per-page images.

    Note: Textract's synchronous `DetectDocumentText` does not accept PDF bytes directly.
    We convert each page to an image locally and send images to Textract.
    """
    require_textract = os.getenv("TDF_REQUIRE_TEXTRACT", "1").strip().lower() not in {"0", "false", "no"}
    if convert_from_path is None:
        if require_textract:
            raise RuntimeError("pdf2image is not installed; cannot run Textract PDF extraction.")
        return ""

    try:
        # dpi=200 is a balance between quality and payload size.
        pages = convert_from_path(str(path), dpi=200)
        if not pages:
            return ""
        lines: list[str] = []
        for i, page in enumerate(pages, start=1):
            buf = io.BytesIO()
            page.save(buf, format="PNG")
            page_text = extract_with_textract(buf.getvalue(), f"{path.name} (page {i})")
            if page_text.strip():
                lines.append(page_text.strip())
        return "\n\n".join(lines)
    except Exception as exc:
        if require_textract:
            raise
        print(f"  Textract PDF extraction failed for {path.name}: {exc}")

    if PdfReader is None:
        print("  PyPDF is not installed; skipping PDF fallback.")
        return ""

    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(page_text.strip())
        return "\n\n".join(pages)
    except Exception as exc:
        print(f"  PyPDF fallback failed for {path.name}: {exc}")
        return ""


def extract_xlsx_text(path: Path) -> str:
    """Extract text from an Excel workbook using pandas."""
    try:
        sheets = pd.read_excel(path, sheet_name=None)
        rendered = []
        for sheet_name, df in sheets.items():
            rendered.append(f"# Sheet: {sheet_name}")
            try:
                rendered.append(df.to_markdown(index=False))
            except Exception:
                rendered.append(df.to_csv(index=False))
        return "\n\n".join(rendered)
    except Exception as exc:
        print(f"  Unable to read Excel {path.name}: {exc}")
        return ""


def extract_image_text(path: Path) -> str:
    """Extract text from an image using Textract."""
    require_textract = os.getenv("TDF_REQUIRE_TEXTRACT", "1").strip().lower() not in {"0", "false", "no"}
    try:
        img_bytes = path.read_bytes()
        return extract_with_textract(img_bytes, path.name)
    except Exception as exc:
        if require_textract:
            raise
        print(f"  Textract failed for {path.name}: {exc}")
        return ""


Extractor = Callable[[Path], str]

EXTRACTORS: Dict[str, Extractor] = {
    ".pdf": extract_pdf_text,
    ".xlsx": extract_xlsx_text,
    ".png": extract_image_text,
    ".jpg": extract_image_text,
    ".jpeg": extract_image_text,
}


def collect_supported_files(files_dir: Path) -> Iterable[Path]:
    """Yield supported files from the provided directory."""
    for path in sorted(files_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_FILE_EXTS:
            yield path


def extract_text_for_file(path: Path) -> str:
    """Dispatch to the appropriate extractor based on file extension."""
    extractor = EXTRACTORS.get(path.suffix.lower())
    if not extractor:
        return ""
    return extractor(path).strip()


def write_output(text: str, source_path: Path, output_dir: Path) -> Path:
    """Write extracted text to a file named after the source file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{source_path.stem}.txt"
    output_path.write_text(text, encoding="utf-8")
    return output_path


def main(files_dir: Path, output_dir: Path) -> None:
    """Iterate through files and extract their text into the output directory."""
    if not files_dir.exists():
        print(f"Files directory not found: {files_dir}")
        return

    supported_files = list(collect_supported_files(files_dir))
    if not supported_files:
        print(f"No supported files found in {files_dir}")
        return

    saved_files = []
    for path in supported_files:
        print(f"Extracting text from {path.name} ...")
        text = extract_text_for_file(path)
        if not text:
            print(f"  No text extracted from {path.name}")
            continue

        output_path = write_output(text, path, output_dir)
        saved_files.append(output_path)
        print(f"  Saved to {output_path}")

    print(f"\nExtraction complete: {len(saved_files)} of {len(supported_files)} files had text.")
    if saved_files:
        print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract text from files in ./files using Textract (pandas for XLSX)."
    )
    parser.add_argument(
        "--files-dir",
        type=Path,
        default=DEFAULT_FILES_DIR,
        help="Directory containing files to extract text from.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to write extracted .txt files.",
    )
    args = parser.parse_args()

    main(args.files_dir, args.output_dir)
