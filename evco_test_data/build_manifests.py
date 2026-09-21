"""
Builds evco_test_data/manifests/row_test_manifest.json and business_rule_coverage.md
from data that already exists (does not invent new scenario/rule data):

  - evco_mock_data/data/mock_quote_data.json   (full per-row field data: box_qty,
    tiers[moq, price], material, description, lead_time, scenarios[], annotation)
  - evco_mock_data/mock_quote_manifest.json    (quote-level header/customer info)
  - evco_mock_data/data/rule_catalog.py        (RULE_CATALOG, rules_for_scenarios,
    quote_level_rules - imported, not re-implemented)

This script only reshapes existing data into the row-level test manifest format
requested for evco_test_data/ (PDF, Quote#, Row#, EVCO PN, MFG/BOM, Customer PN,
Quantity, MOQ, Box Qty, Price, Effective Date, Scenario, Rule Code(s), Rule
Name(s), Expected Result, Scenario Type, Required API/database state).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
MOCK_DATA_DIR = PROJECT_ROOT / "evco_mock_data"

sys.path.insert(0, str(PROJECT_ROOT))
from evco_mock_data.data.rule_catalog import (  # noqa: E402
    RULE_CATALOG,
    quote_level_rules,
    rules_for_scenarios,
)

SCENARIO_TYPE_MAP = {
    "VALID_BASELINE": "valid",
    "MULTI_TIER_PRICING": "valid",
    "SINGLE_TIER_PRICING": "valid",
    "BOM_RESIN_VARIANT": "bom_manufacturing",
    "BOM_COMBINED_PLUS": "bom_manufacturing",
    "BOM_SUFFIX_VARIANT": "bom_manufacturing",
    "MOQ_LOW_BOUNDARY": "boundary",
    "MOQ_EQUALS_BOX_QTY": "boundary",
    "MOQ_HIGH_VOLUME": "boundary",
    "BOX_QTY_MOQ_NOT_DIVISIBLE": "warning",
    "AKA_QTY_BREAK_OF_ONE": "boundary",
    "BLANK_CUSTOMER_PN": "exception",
    "BLANK_DESCRIPTION": "exception",
    "BLANK_MOQ_SINGLE_ROW": "exception",
    "BLANK_PRICE_SINGLE_ROW": "exception",
    "LONG_DESCRIPTION_WRAP": "extraction_robustness",
    "LEAD_TIME_EXTENDED": "informational",
    "COMBINED_MULTI_RULE": "multi_rule",
    "SPECIAL_CHAR_PART_NUMBER": "extraction_robustness",
    "DECOY_QUANTITY_COLUMN_ROW": "extraction_robustness",
    "RESIN_MATERIAL_ROW": "excluded_from_extraction",
    "PRICE_INCREASE_HIGHLIGHT": "price_change",
    "PRICE_UNCHANGED_ROW": "price_change",
    "STRUCK_ROW_RED_ANNOTATION": "annotation",
    "STRUCK_ROW_PINK_ANNOTATION": "annotation",
    "CELL_OVERRIDE_RED_BOX": "annotation",
}


def required_api_db_state(quote, row, rule_codes):
    """Short, human-readable description of what fixture/DB state this row
    depends on to be exercised meaningfully - derived from the row's own
    scenario tags and the quote-level flags already present in
    mock_quote_data.json, not invented per-row."""
    notes = []
    customer = quote["customer"]["name"] if isinstance(quote.get("customer"), dict) else quote.get("customer")

    if "EX-001" in rule_codes:
        notes.append("Quote type is Inactive - BR-001 should stop processing before any API lookups occur.")
        return notes
    if "EX-002" in rule_codes or "EX-003" in rule_codes:
        notes.append(
            f"Quote header/table is missing '{quote.get('missing_required_column') or 'a required field'}' - "
            "extraction should fail fast (validate_before_extraction) before any downstream API call."
        )
        return notes

    notes.append(f"Customer fixture (mock_data/iqms_customers_lite.json) must resolve '{customer}' via CustomerRepository.")
    if quote.get("customer_similarity_test"):
        notes.append(
            "This quote's customer name is the deliberately ambiguous 'Align Technology' duplicate - "
            "fixture must contain both IQMS records (11965 and DH102) for EX-006/VR-006 to be exercised."
        )

    if row.get("bom"):
        notes.append(f"AKA/BOM fixture (mock_data/aka_inventory/) must expose BOM '{row['bom']}' for EVCO PN '{row['evco_pn']}'.")

    tiers = row.get("tiers") or []
    if len(tiers) > 1:
        notes.append(f"Pricing fixture must return {len(tiers)} quantity-break tiers for this EVCO PN/BOM.")

    if row.get("annotation"):
        ann_type = row["annotation"].get("type") if isinstance(row["annotation"], dict) else None
        if ann_type in ("strike_red", "strike_pink"):
            notes.append("Row is struck in the PDF - extraction must exclude it entirely (ANN-STRIKE); no API lookup expected.")
        elif ann_type == "override_box":
            notes.append("Row has a cell override box - the overridden value, not the printed one, must be used downstream (ANN-OVERRIDE).")

    if quote.get("supersedes_prior_quote"):
        notes.append(
            "Quote supersedes a prior quote for this customer/part - pricing_history fixture/table must already "
            "contain a prior active price so BR-015/016 (preserve history, don't overwrite) can be exercised."
        )

    return notes


def main():
    quotes_data = json.loads((MOCK_DATA_DIR / "data" / "mock_quote_data.json").read_text(encoding="utf-8"))
    quote_manifest = {q["file"]: q for q in json.loads((MOCK_DATA_DIR / "mock_quote_manifest.json").read_text(encoding="utf-8"))["quotes"]}

    output_quotes = []
    coverage_hits = {}  # rule_code -> count of rows exercising it

    for quote in quotes_data:
        fname = quote["file"]
        header = quote_manifest[fname]
        q_rules = quote_level_rules(quote)
        for code in q_rules:
            coverage_hits[code] = coverage_hits.get(code, 0) + 1

        out_rows = []
        for row in quote.get("rows", []):
            scenarios = row.get("scenarios", [])
            row_rules = sorted(set(rules_for_scenarios(scenarios)) | (set(q_rules) if row is quote["rows"][0] else set()))
            # Row-level rules = scenario-derived rules only (quote-level rules are reported once, at quote level,
            # not duplicated onto every row) - recompute cleanly:
            row_rules = rules_for_scenarios(scenarios)
            for code in row_rules:
                coverage_hits[code] = coverage_hits.get(code, 0) + 1

            tiers = row.get("tiers", [])
            first_tier = tiers[0] if tiers else {}
            scenario_types = sorted({SCENARIO_TYPE_MAP.get(s, "other") for s in scenarios})

            expected_result = "VALID_STRUCTURAL_INPUT"
            if any(SCENARIO_TYPE_MAP.get(s) == "exception" for s in scenarios):
                expected_result = "EXPECTED_EXCEPTION"
            elif any(SCENARIO_TYPE_MAP.get(s) == "warning" for s in scenarios):
                expected_result = "EXPECTED_WARNING"
            elif row.get("annotation") and row["annotation"].get("type") in ("strike_red", "strike_pink"):
                expected_result = "EXCLUDED_FROM_EXTRACTION"

            out_rows.append({
                "pdf": fname,
                "quote_number": header["quote_number"],
                "row_number": row["row"],
                "evco_part_number": row.get("evco_pn"),
                "manufacturing_bom_number": row.get("bom"),
                "customer_part_number": row.get("customer_pn"),
                "box_qty": row.get("box_qty"),
                "moq": first_tier.get("moq"),
                "price": first_tier.get("price"),
                "all_tiers": tiers,
                "effective_date": header.get("price_effective_date"),
                "scenario": scenarios,
                "scenario_type": scenario_types,
                "rule_codes": row_rules,
                "rule_names": [RULE_CATALOG[c]["title"] for c in row_rules if c in RULE_CATALOG],
                "expected_result": expected_result,
                "required_api_db_state": required_api_db_state(quote, row, row_rules),
                "notes": row.get("notes"),
            })

        output_quotes.append({
            "file": fname,
            "quote_number": header["quote_number"],
            "customer_name": header["customer_name"],
            "quote_type": header["type"],
            "quote_level_rule_codes": q_rules,
            "quote_level_rule_names": [RULE_CATALOG[c]["title"] for c in q_rules if c in RULE_CATALOG],
            "quote_level_exception": header.get("quote_level_exception"),
            "rows": out_rows,
        })

    (ROOT / "manifests" / "row_test_manifest.json").write_text(
        json.dumps({"quotes": output_quotes}, indent=2), encoding="utf-8"
    )

    # --- business_rule_coverage.md -----------------------------------------
    lines = [
        "# Business Rule Coverage — evco_test_data",
        "",
        "Rule codes exercised across the 5 reused PDFs (`evco_mock_data/pdfs/`), by row/quote count.",
        "Source of truth: `evco_mock_data/data/rule_catalog.py` (not duplicated here).",
        "",
        "| Rule Code | Category | Title | Verifiability | Rows/Quotes exercising it |",
        "|---|---|---|---|---|",
    ]
    for code in sorted(RULE_CATALOG):
        info = RULE_CATALOG[code]
        count = coverage_hits.get(code, 0)
        lines.append(f"| {code} | {info['category']} | {info['title']} | {info['verifiability']} | {count} |")
    uncovered = [c for c in RULE_CATALOG if coverage_hits.get(c, 0) == 0]
    lines.append("")
    if uncovered:
        lines.append(f"**Not exercised by any row/quote in this dataset:** {', '.join(uncovered)}")
    else:
        lines.append("**All cataloged rule codes are exercised by at least one row or quote.**")
    (ROOT / "manifests" / "business_rule_coverage.md").write_text("\n".join(lines), encoding="utf-8")

    total_rows = sum(len(q["rows"]) for q in output_quotes)
    print(f"Wrote row_test_manifest.json ({total_rows} rows across {len(output_quotes)} quotes)")
    print(f"Wrote business_rule_coverage.md ({len(RULE_CATALOG)} rule codes, {len(uncovered)} uncovered)")


if __name__ == "__main__":
    main()
