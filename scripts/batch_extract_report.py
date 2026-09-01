"""
Batch-extract every PDF in a folder through the production extraction
pipeline (app.modules.extraction.parser.extract_pdf_data - the exact same
function POST /api/extract-quote uses) and write a summary CSV report plus
one JSON file per document with its full extracted data.

This does NOT write anything to the database - it's a standalone,
read-only batch run over a folder of PDFs, for reviewing extraction
quality/exceptions across many quotes at once.

Configure the paths below, then run from the project root:
    python scripts/batch_extract_report.py
"""

import csv
import json
import sys
import time
from pathlib import Path

# --- Configuration - edit these -----------------------------------------
FOLDER_PATH = r"C:\Data\testing"                     # folder to scan for PDFs
RECURSIVE = False                                  # also scan subfolders
OUTPUT_CSV_PATH = r"C:\Data\extraction_report.csv"  # summary report
SAVE_EXTRACTED_JSON = True                          # save each file's full extracted JSON
JSON_OUTPUT_DIR = r"C:\Data\extraction_results"     # where those JSON files go
# --------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # noqa: E402

from app.modules.extraction.parser import extract_pdf_data  # noqa: E402

CSV_FIELDS = ["Quote Name", "EXCEPTION code", "Pages", "Time taken", "Exception message"]


def find_pdfs(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(folder.glob(pattern))


def count_pages(pdf_path: Path) -> int | None:
    try:
        with fitz.open(str(pdf_path)) as doc:
            return len(doc)
    except Exception:
        return None


def process_one(pdf_path: Path) -> dict:
    pages = count_pages(pdf_path)
    started = time.perf_counter()
    try:
        result = extract_pdf_data(str(pdf_path))
        crash_text = None
    except Exception as e:
        result = None
        crash_text = str(e)
    elapsed = time.perf_counter() - started

    if result is None:
        exception_code = "SCRIPT_ERROR"
        exception_message = crash_text or "Unhandled exception during extraction"
        print(f"CRASHED ({elapsed:.1f}s): {exception_message}")
    else:
        status = result.get("status", "success")
        if status in ("exception", "extraction_failed"):
            exception_code = ", ".join(result.get("exception_codes") or []) or status.upper()
            exception_message = result.get("exception_reason") or ""
            print(f"EXCEPTION [{exception_code}] ({elapsed:.1f}s)")
        else:
            exception_code = ""
            exception_message = ""
            print(f"OK - {len(result.get('parts') or [])} part(s) ({elapsed:.1f}s)")

        if SAVE_EXTRACTED_JSON:
            out_path = Path(JSON_OUTPUT_DIR) / f"{pdf_path.stem}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    return {
        "Quote Name": pdf_path.name,
        "EXCEPTION code": exception_code,
        "Pages": pages if pages is not None else "",
        "Time taken": f"{elapsed:.1f}s",
        "Exception message": exception_message,
    }


def main():
    folder = Path(FOLDER_PATH)
    if not folder.is_dir():
        print(f"Folder not found: {folder}")
        return

    pdfs = find_pdfs(folder, RECURSIVE)
    if not pdfs:
        print(f"No PDF files found in {folder}")
        return

    if SAVE_EXTRACTED_JSON:
        Path(JSON_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    print(f"Found {len(pdfs)} PDF file(s) in {folder}\n")

    rows = []
    batch_started = time.perf_counter()
    for idx, pdf_path in enumerate(pdfs, start=1):
        print(f"[{idx}/{len(pdfs)}] {pdf_path.name} ...", end=" ", flush=True)
        rows.append(process_one(pdf_path))
    batch_elapsed = time.perf_counter() - batch_started

    with open(OUTPUT_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    exception_count = sum(1 for r in rows if r["EXCEPTION code"])
    print(f"\nDone in {batch_elapsed:.1f}s. {len(rows)} file(s) processed, {exception_count} with an exception.")
    print(f"Report written to: {OUTPUT_CSV_PATH}")
    if SAVE_EXTRACTED_JSON:
        print(f"Full extracted JSON per file written to: {JSON_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
