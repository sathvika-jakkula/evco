"""
Builds ONE master AKA-inventory + pricing mock-data file
(evco_test_data/mock_data/aka_inventory.json) covering every unique EVCO part
number that appears across all 5 reused dummy quote PDFs
(evco_mock_data/data/mock_quote_data.json).

Shape is per the user's own spec (not this project's existing internal
AkaRecordData contract - this is upstream "EVCO/IQMS master data" mock, one
level below where InventoryService/PriceBreakService sit):

{
  "items": [
    {
      "data": {
        "Header": {"Item #": ..., "Rev": ..., "Description": ...},
        "akaDetails": [ {akaItem#, akaDescription, rev, customer#, currency,
                          customername, mfg#, shipToAttn, minimumSellingQty,
                          sellingMultiplesOf}, ... ]
      },
      "pricingDetails": [ {price, qty, effectiveDate, Comment, inactiveDate,
                            priceDate}, ... ]
    },
    ...
  ]
}

Deliberately blank fields (per instruction, not a bug):
  - akaItem# is left "" for rows tagged BLANK_CUSTOMER_PN in the source data
    (the PDF itself prints no customer part number on that row).
  - mfg# is left "" for every row in EVCO_QUOTE_74025_DUMMY.pdf, whose
    quote-level missing_required_column is manufacturing_bom_number (EX-002/
    EX-007/EX-008/VR-009 test material) - the PDF's table never prints an
    EVCO MFG/BOM column at all for this quote.
  - inactiveDate is populated only for the FIRST tier of rows belonging to a
    quote flagged supersedes_prior_quote=true, simulating a prior price
    version that should already be inactive (BR-015/BR-016 history test
    material) - all other tiers are currently-active (inactiveDate "").

Every akaDetails/pricingDetails entry also carries "_comment" annotation
fields (underscore-prefixed so FixtureIQMSClient and any real consumer can
ignore them - they are documentation, not part of the IQMS-shaped payload):
  "_scenario"        - this dataset's internal scenario tag(s) for the source row
  "_ruleCodes"        - official BR/BRM/VR/EX/ANN rule code(s) that apply, from
                        evco_mock_data/data/rule_catalog.py (not re-derived here)
  "_ruleNames"        - human-readable titles for those codes
  "_hasException"     - true if this row/quote is expected to trigger an
                        Exception Rule (EX-xxx)
  "_note"             - the source row's own generation note, for traceability

This is data only - no application/service code is added or changed.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
MOCK_DATA_DIR = ROOT.parent / "evco_mock_data"

sys.path.insert(0, str(PROJECT_ROOT))
from evco_mock_data.data.rule_catalog import RULE_CATALOG, quote_level_rules, rules_for_scenarios  # noqa: E402


def clean_price(price_str):
    return float(str(price_str).replace("$", "").replace(",", ""))


def clean_int(value):
    if value in (None, ""):
        return 0
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return 0


def main():
    quotes = json.loads((MOCK_DATA_DIR / "data" / "mock_quote_data.json").read_text(encoding="utf-8"))

    items = {}  # evco_pn -> item dict
    for quote in quotes:
        customer = quote["customer"]
        blank_mfg_quote = quote.get("missing_required_column") == "manufacturing_bom_number"
        supersedes = bool(quote.get("supersedes_prior_quote"))
        effective_date = quote.get("price_effective_date") or ""
        q_rule_codes = quote_level_rules(quote)
        quote_has_exception = any(c.startswith("EX-") for c in q_rule_codes)
        # Only the FIRST eligible row of a supersedes_prior_quote=true quote gets a
        # prior-inactive-version tier - this is one representative BR-015/BR-016
        # example per quote, not a blanket flag applied to every row in it.
        prior_version_example_emitted = False

        for row in quote.get("rows", []):
            pn = row.get("evco_pn")
            if not pn:
                continue

            blank_aka_item = "BLANK_CUSTOMER_PN" in (row.get("scenarios") or [])
            aka_item_number = "" if blank_aka_item else (row.get("customer_pn") or "")
            mfg_number = "" if blank_mfg_quote else (row.get("bom") or "")

            scenarios = row.get("scenarios") or []
            row_rule_codes = sorted(set(rules_for_scenarios(scenarios)) | set(q_rule_codes))
            row_has_exception = quote_has_exception or any(c.startswith("EX-") for c in row_rule_codes)

            item = items.setdefault(pn, {
                "data": {
                    "Header": {
                        "Item #": pn,
                        "Rev": "A",
                        "Description": row.get("description") or "",
                    },
                    "akaDetails": [],
                },
                "pricingDetails": [],
            })

            item["data"]["akaDetails"].append({
                "akaItem#": aka_item_number,
                "akaDescription": row.get("description") or "",
                "rev": "A",
                "customer#": customer["customer_number"],
                "currency": "US Dollar",
                "customername": customer["name"],
                "mfg#": mfg_number,
                "shipToAttn": "",
                "minimumSellingQty": clean_int(row["tiers"][0]["moq"]) if row.get("tiers") else 0,
                "sellingMultiplesOf": clean_int(row["box_qty"]) if row.get("box_qty") else 0,
                # The quote PDF's "Mold" column - called soItemNumber in the AKA API
                # contract, not mold, since that's the name the customer/IQMS side uses.
                "soItemNumber": row.get("mold") or "",
                "_scenario": scenarios,
                "_ruleCodes": row_rule_codes,
                "_ruleNames": [RULE_CATALOG[c]["title"] for c in row_rule_codes if c in RULE_CATALOG],
                "_hasException": row_has_exception,
                "_note": row.get("notes") or "",
            })

            tiers = row.get("tiers") or []
            for idx, tier in enumerate(tiers):
                if not tier.get("moq") or not tier.get("price"):
                    # BLANK_MOQ_SINGLE_ROW / BLANK_PRICE_SINGLE_ROW scenario rows: no
                    # pricingDetails entry can be built, but still record why here.
                    continue
                is_prior_version = supersedes and idx == 0 and not prior_version_example_emitted
                if is_prior_version:
                    prior_version_example_emitted = True
                tier_rule_codes = list(row_rule_codes)
                if is_prior_version and "BR-016" not in tier_rule_codes:
                    tier_rule_codes = sorted(set(tier_rule_codes) | {"BR-015", "BR-016"})
                item["pricingDetails"].append({
                    "price": clean_price(tier["price"]),
                    "qty": clean_int(tier["moq"]),
                    "effectiveDate": effective_date,
                    "Comment": row.get("bom") or "",
                    "inactiveDate": effective_date if is_prior_version else "",
                    "priceDate": effective_date,
                    "_scenario": scenarios,
                    "_ruleCodes": tier_rule_codes,
                    "_ruleNames": [RULE_CATALOG[c]["title"] for c in tier_rule_codes if c in RULE_CATALOG],
                    "_hasException": row_has_exception,
                    "_note": (
                        "Prior price version, superseded by this quote - see BR-015/BR-016."
                        if is_prior_version else (row.get("notes") or "")
                    ),
                })

    output = {"items": [{"data": v["data"], "pricingDetails": v["pricingDetails"]} for v in items.values()]}

    out_path = ROOT / "mock_data" / "aka_inventory.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} - {len(items)} unique EVCO part numbers, "
          f"{sum(len(v['data']['akaDetails']) for v in items.values())} akaDetails rows, "
          f"{sum(len(v['pricingDetails']) for v in items.values())} pricingDetails rows")


if __name__ == "__main__":
    main()
