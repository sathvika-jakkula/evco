"""
Validate the generated EVCO mock quote PDFs against:
  1. The generation metadata (mock_quote_manifest.json / mock_quote_row_manifest.json).
  2. The REAL project's own deterministic extraction code
     (app/modules/extraction/parser.py) - imported directly, not re-implemented -
     so a pass here means the actual code the project will run against these
     PDFs behaves the way this dataset claims it does.

What this script CANNOT validate (documented, not silently skipped):
  - The LLM-based parts[]/pricing_tiers extraction (parser.call_llm / IBM API).
    That requires live IBM_API_KEY/IBM_BASE_URL credentials this environment
    does not have. Everything checked here runs BEFORE that call
    (has_image_only_table, extract_header_fields, find_master_table_header,
    validate_before_extraction, get_page_annotations, find_effective_date_override)
    and is completely deterministic Python - no network/model call involved.
  - Business/validation rules whose own spec marks them as evaluated by agent
    reasoning rather than this repository's code (see business_rule_plan.md).

Usage:
    python validate_mock_quotes.py
Exit code is non-zero if any check fails.
"""

import json
import subprocess
import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
PDF_DIR = ROOT / "pdfs"

sys.path.insert(0, str(PROJECT_ROOT))
from app.modules.extraction import parser as p  # noqa: E402
from app.modules.customer.repository import CustomerRepository  # noqa: E402

failures = []
warnings = []


class _FixtureIQMSClient:
    """Stands in for a live IQMSClient, serving the local CustomersLite export
    so CustomerRepository's real matching logic can be exercised without a
    live IQMS connection."""

    def __init__(self, records):
        self._records = records

    def get_customers_lite(self):
        return self._records


def check_customer_resolution():
    """VR-005/VR-006/EX-006: run the REAL CustomerRepository.find_candidates()
    against the local IQMS CustomersLite export, not a re-implementation."""
    fixture = json.loads((ROOT / "data" / "iqms_customers_lite.json").read_text(encoding="utf-8"))["data"]
    repo = CustomerRepository(iqms_client=_FixtureIQMSClient(fixture))

    # PDF2's customer ("ALIGN TECHNOLOGY INC") is long/suffixed enough that
    # searching by name alone resolves to it uniquely.
    unique = repo.find_candidates(customer_name="ALIGN TECHNOLOGY INC", customer_number=None)
    check(len(unique) == 1 and unique[0].customer_number == "11965",
          f"Expected a unique match for 'ALIGN TECHNOLOGY INC', got {[(c.customer_name, c.customer_number) for c in unique]}")

    # PDF3's customer ("Align Technology") is the short form of the SAME real
    # company under a second IQMS record (DH102) - searching by name alone
    # must be AMBIGUOUS (EX-006), matching both records.
    ambiguous = repo.find_candidates(customer_name="Align Technology", customer_number=None)
    check(len(ambiguous) == 2,
          f"Expected an AMBIGUOUS match (2 candidates) for 'Align Technology', got "
          f"{[(c.customer_name, c.customer_number) for c in ambiguous]}")

    # VR-006: preferring the unique customer number over the ambiguous name
    # resolves PDF3's customer unambiguously.
    by_number = repo.find_candidates(customer_name="Align Technology", customer_number="DH102")
    check(len(by_number) == 1 and by_number[0].customer_number == "DH102",
          f"Expected customer_number='DH102' to resolve uniquely, got "
          f"{[(c.customer_name, c.customer_number) for c in by_number]}")

    # PDF1/4/5's customers should each resolve uniquely with no ambiguity.
    for name, number in [("CLACK CORPORATION", "10329"), ("GREENHECK FAN CORP", "11853"),
                          ("HARLEY-DAVIDSON MOTOR CO", "11967")]:
        candidates = repo.find_candidates(customer_name=name, customer_number=None)
        check(len(candidates) == 1 and candidates[0].customer_number == number,
              f"Expected a unique match for {name!r}, got {[(c.customer_name, c.customer_number) for c in candidates]}")


def check(condition, message):
    if not condition:
        failures.append(message)
    return condition


def warn(message):
    warnings.append(message)


def main():
    quote_manifest = json.loads((ROOT / "mock_quote_manifest.json").read_text(encoding="utf-8"))
    row_manifest = json.loads((ROOT / "mock_quote_row_manifest.json").read_text(encoding="utf-8"))
    row_manifest_by_file = {q["file"]: q for q in row_manifest["quotes"]}

    expected_files = {q["file"] for q in quote_manifest["quotes"]}
    actual_files = {f.name for f in PDF_DIR.glob("*.pdf")}
    check(expected_files == actual_files,
          f"PDF set mismatch: expected {expected_files}, found {actual_files}")

    for q in quote_manifest["quotes"]:
        fname = q["file"]
        pdf_path = PDF_DIR / fname
        print(f"\n=== {fname} ===")

        check(fname.endswith("_DUMMY.pdf"), f"{fname}: filename must end with _DUMMY.pdf")
        if not pdf_path.exists():
            failures.append(f"{fname}: file does not exist in pdfs/")
            continue

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:
            failures.append(f"{fname}: could not open with PyMuPDF: {exc}")
            continue

        check(doc.page_count == q["page_count"],
              f"{fname}: manifest page_count={q['page_count']} but PDF has {doc.page_count} pages")

        full_text = "\n".join(doc[i].get_text("text") for i in range(doc.page_count))
        check(len(full_text.strip()) > 500,
              f"{fname}: extracted plain text looks too short ({len(full_text)} chars) - "
              f"is this PDF actually machine-readable text, not an image?")

        # --- has_image_only_table (real code) --------------------------------
        is_image_only = p.has_image_only_table(doc)
        check(is_image_only is False, f"{fname}: has_image_only_table() returned True - "
              "this mock PDF must be genuine text, not a rendered image.")

        # --- extract_header_fields (real code) --------------------------------
        header_fields = p.extract_header_fields(full_text)
        expected_quote_number = q["quote_number"].replace(" ", "")
        check(header_fields.get("quote_number") == expected_quote_number,
              f"{fname}: extract_header_fields quote_number={header_fields.get('quote_number')!r}, "
              f"expected {expected_quote_number!r}")
        check(header_fields.get("type") == q["type"],
              f"{fname}: extract_header_fields type={header_fields.get('type')!r}, expected {q['type']!r}")
        check(header_fields.get("customer_name") == q["customer_name"],
              f"{fname}: extract_header_fields customer_name={header_fields.get('customer_name')!r}, "
              f"expected {q['customer_name']!r}")

        override = p.find_effective_date_override(full_text)
        if override:
            check(header_fields.get("price_effective_date") == override,
                  f"{fname}: extract_header_fields did not apply the detected 'Effective to' override "
                  f"({override!r}) to price_effective_date")

        # --- find_master_table_header + validate_before_extraction (real code) ---
        master_header = p.find_master_table_header(doc)
        pre_failures = p.validate_before_extraction(header_fields, master_header)
        actual_codes = sorted({c for f in pre_failures for c in f["codes"]})
        expected_exception = q.get("quote_level_exception")
        expected_codes = [expected_exception] if expected_exception else []
        check(actual_codes == expected_codes,
              f"{fname}: validate_before_extraction() returned codes {actual_codes}, "
              f"expected {expected_codes} (manifest quote_level_exception={expected_exception!r}). "
              f"Messages: {[f['message'] for f in pre_failures]}")

        # --- get_page_annotations (real code) - strike rows / override boxes ---
        total_strike = total_override = 0
        for i in range(doc.page_count):
            ann = p.get_page_annotations(doc[i])
            total_strike += len(ann["strike_rows"])
            total_override += len(ann["override_boxes"])

        row_entries = row_manifest_by_file[fname]["rows"]
        expected_strike = sum(
            1 for r in row_entries if r["annotation"] and r["annotation"]["type"] in ("strike_red", "strike_pink")
        )
        expected_override = sum(
            1 for r in row_entries if r["annotation"] and r["annotation"]["type"] == "override_box"
        )
        check(total_strike == expected_strike,
              f"{fname}: get_page_annotations() found {total_strike} strike_rows, expected {expected_strike}")
        check(total_override == expected_override,
              f"{fname}: get_page_annotations() found {total_override} override_boxes, expected {expected_override}")

        # --- row-level manifest sanity (data-model level, not LLM-verifiable) ---
        pricing_row_count = len(row_entries)
        check(pricing_row_count >= 50,
              f"{fname}: only {pricing_row_count} pricing rows, need >= 50")
        check(pricing_row_count == q["pricing_row_count"],
              f"{fname}: row manifest has {pricing_row_count} rows but quote manifest says "
              f"{q['pricing_row_count']}")
        for r in row_entries:
            if not r["scenario"]:
                failures.append(f"{fname} row {r['row']}: no scenario tag assigned")

        doc.close()

    print("\n=== Customer resolution (real CustomerRepository + local IQMS fixture) ===")
    check_customer_resolution()

    # --- git integrity check: existing project must be untouched ---------------
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "app", "requirements.txt", "openapi", "scripts"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30,
        )
        dirty = [line for line in result.stdout.splitlines() if line.strip()]
        # iqms.py was already modified before this task started (pre-existing,
        # unrelated working-tree change) - exclude it from the "did we touch
        # anything" check rather than treating pre-existing state as a failure.
        dirty = [line for line in dirty if "iqms.py" not in line]
        check(not dirty, f"Existing project files show unexpected changes: {dirty}")
    except Exception as exc:
        warn(f"Could not run git status integrity check: {exc}")

    print("\n" + "=" * 70)
    if warnings:
        print(f"{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    main()
