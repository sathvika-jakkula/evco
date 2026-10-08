"""
Appends ONLY the new 65150-003 quote's AKA/pricing/sales-order data onto the
EXISTING committed mock-data files, instead of regenerating those files from
scratch via build_aka_master.py / build_sales_order_master.py.

Why: the committed evco_test_data/mock_data/aka_inventory.json has at least
one hand-patched value (item 9480026's first pricing tier Comment was changed
from "6601/9480026-CHIMEI" to "9440502 + 9440503", qty/price also changed)
that app/tests/test_api_terminology.py::test_pricing_lookup_add_update_contract
depends on directly. Re-running the builder scripts overwrites that hand
patch and breaks the test. This script preserves all existing file content
and only adds new items/records for the 65150-003 quote.

Reuses the exact same per-row logic as build_aka_master.py (including the
AKA_NOT_FOUND_CREATE_TEST / AKA_STALE_UPDATE_TEST / PRICE_STALE_UPDATE_TEST
scenario handling added there) and build_sales_order_master.py, but targets
only the one new quote and merges into the existing JSON structures.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
MOCK_DATA_DIR = ROOT.parent / "evco_mock_data"

import sys
sys.path.insert(0, str(PROJECT_ROOT))
from evco_mock_data.data.rule_catalog import RULE_CATALOG, quote_level_rules, rules_for_scenarios  # noqa: E402

QUOTE_FILE = "65150-003.pdf"
AKA_PATH = ROOT / "mock_data" / "aka_inventory.json"
SO_DIR = ROOT / "mock_data" / "sales_orders"


def clean_price(price_str):
    return float(str(price_str).replace("$", "").replace(",", ""))


def clean_int(value):
    if value in (None, ""):
        return 0
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return 0


def build_aka_contribution(quote):
    """Same per-row logic as build_aka_master.py's main(), scoped to one quote."""
    customer = quote["customer"]
    blank_mfg_quote = quote.get("missing_required_column") == "manufacturing_bom_number"
    supersedes = bool(quote.get("supersedes_prior_quote"))
    effective_date = quote.get("price_effective_date") or ""
    q_rule_codes = quote_level_rules(quote)
    quote_has_exception = any(c.startswith("EX-") for c in q_rule_codes)
    prior_version_example_emitted = False

    items = {}
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

        no_aka_record = "AKA_NOT_FOUND_CREATE_TEST" in scenarios
        no_price_record = no_aka_record or "PRICE_NOT_FOUND_CREATE_TEST" in scenarios
        stale_aka = "AKA_STALE_UPDATE_TEST" in scenarios
        stale_price = "PRICE_STALE_UPDATE_TEST" in scenarios

        item = items.setdefault(pn, {
            "data": {
                "Header": {"Item #": pn, "Rev": "A", "Description": row.get("description") or ""},
                "akaDetails": [],
            },
            "pricingDetails": [],
        })

        if not no_aka_record:
            item["data"]["akaDetails"].append({
                "akaItem#": aka_item_number,
                "akaDescription": row.get("description") or "",
                "rev": "A",
                "customer#": customer["customer_number"],
                "currency": "US Dollar",
                "customername": customer["name"],
                "mfg#": mfg_number,
                "shipToAttn": "",
                "minimumSellingQty": (
                    clean_int(row["tiers"][0]["moq"]) + 1 if stale_aka and row.get("tiers")
                    else (clean_int(row["tiers"][0]["moq"]) if row.get("tiers") else 0)
                ),
                "sellingMultiplesOf": clean_int(row["box_qty"]) if row.get("box_qty") else 0,
                "soItemNumber": ("OLD-" + (row.get("mold") or "")) if stale_aka else (row.get("mold") or ""),
                "_scenario": scenarios,
                "_ruleCodes": row_rule_codes,
                "_ruleNames": [RULE_CATALOG[c]["title"] for c in row_rule_codes if c in RULE_CATALOG],
                "_hasException": row_has_exception,
                "_note": row.get("notes") or "",
            })

        tiers = [] if no_price_record else (row.get("tiers") or [])
        for idx, tier in enumerate(tiers):
            if not tier.get("moq") or not tier.get("price"):
                continue
            is_prior_version = supersedes and idx == 0 and not prior_version_example_emitted
            if is_prior_version:
                prior_version_example_emitted = True
            tier_rule_codes = list(row_rule_codes)
            if is_prior_version and "BR-016" not in tier_rule_codes:
                tier_rule_codes = sorted(set(tier_rule_codes) | {"BR-015", "BR-016"})
            stored_price = round(clean_price(tier["price"]) * 0.85, 2) if stale_price else clean_price(tier["price"])
            item["pricingDetails"].append({
                "price": stored_price,
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

        row["_aka_no_aka_record"] = no_aka_record  # stash for SO builder reuse below

    return items


def build_so_contribution(quote, seq_start):
    """Same per-row logic as build_sales_order_master.py's main(), scoped to one quote."""
    customer = quote["customer"]
    supersedes = bool(quote.get("supersedes_prior_quote"))
    q_rule_codes = quote_level_rules(quote)
    quote_has_exception = any(c.startswith("EX-") for c in q_rule_codes)
    prior_version_example_emitted = False
    customer_mismatch_emitted = False

    sales_orders, details, releases = [], [], []
    seq = seq_start

    for row in quote.get("rows", []):
        pn = row.get("evco_pn")
        tiers = row.get("tiers") or []
        good_tier = next((t for t in tiers if t.get("moq") and t.get("price")), None)
        if not pn or good_tier is None:
            continue

        scenarios = row.get("scenarios") or []
        row_rule_codes = sorted(set(rules_for_scenarios(scenarios)) | set(q_rule_codes))
        row_has_exception = quote_has_exception or any(c.startswith("EX-") for c in row_rule_codes)

        quoted_qty = clean_int(good_tier["moq"])
        quoted_price = clean_price(good_tier["price"])

        so_id, detail_id, ar_invt_id = 500000 + seq, 600000 + seq, 700000 + seq
        seq += 1

        order_qty, unit_price = quoted_qty, quoted_price
        customer_number, company = customer["customer_number"], customer["name"]
        date_taken, so_scenario = "2026-06-20T00:00:00", "correct_quantity_and_price"

        if "MOQ_LOW_BOUNDARY" in scenarios or "BOX_QTY_MOQ_NOT_DIVISIBLE" in scenarios:
            order_qty = max(quoted_qty - 1, 1)
            so_scenario = "quantity_below_moq"
        elif "COMBINED_MULTI_RULE" in scenarios:
            unit_price = round(quoted_price * 1.15, 2)
            so_scenario = "wrong_price"
        elif supersedes and not prior_version_example_emitted:
            prior_version_example_emitted = True
            unit_price = round(quoted_price * 0.9, 2)
            date_taken = "2026-05-01T00:00:00"
            so_scenario = "existing_so_before_quote_price_change"
        elif not customer_mismatch_emitted and False:  # quotes[0] rule N/A for a single appended quote
            pass

        sales_orders.append({
            "Id": so_id, "OrdDetailId": detail_id, "ArInvtId": ar_invt_id,
            "OrderNumber": f"SO-{so_id}", "PONumber": f"PO-{customer['customer_number']}-{row['row']}",
            "CustomerNumber": customer_number, "Company": company,
            "ItemNumber": pn, "Description": row.get("description") or "",
            "CustomerItemNumber": row.get("customer_pn") or "", "CustomerDescription": row.get("description") or "",
            "Status": "Open", "Rev": "A",
            "TotalQTYOrdered": order_qty, "UnitPrice": unit_price,
            "DateTaken": date_taken, "DeliveryDate": "2026-08-15T00:00:00",
            "_scenario": scenarios, "_ruleCodes": row_rule_codes,
            "_ruleNames": [RULE_CATALOG[c]["title"] for c in row_rule_codes if c in RULE_CATALOG],
            "_hasException": row_has_exception, "_soScenario": so_scenario,
            "_note": f"quoted_qty={quoted_qty}, quoted_price={quoted_price} (from {quote['file']} row {row['row']})",
        })

        details.append({
            "Id": detail_id, "SalesOrderId": so_id, "ArInvtId": ar_invt_id,
            "BlanketQty": order_qty, "UnitPrice": unit_price, "ListUnitPrice": quoted_price,
            "UOM": "EA", "OnHold": False, "ShipHold": False, "Discount": 0,
            "Containers": None, "DropShip": False, "POInfo": f"PO-{customer['customer_number']}-{row['row']}",
            "CUser1": so_scenario,
            "_ruleCodes": row_rule_codes, "_soScenario": so_scenario,
        })

        if so_scenario == "correct_quantity_and_price":
            first_qty = order_qty // 2
            release_plan = [(first_qty, "2026-08-30T00:00:00"), (order_qty - first_qty, "2026-09-29T00:00:00")]
        else:
            release_plan = [(order_qty, "2026-08-30T00:00:00")]

        release_seq = 1
        for release_qty, must_ship in release_plan:
            releases.append({
                "Id": 800000 + detail_id * 2 + release_seq, "SalesOrderDetailId": detail_id, "ArInvtId": ar_invt_id,
                "Seq": release_seq, "Qty": release_qty,
                "OriginalQty": order_qty, "ShippedQty": 0, "LeftToShip": release_qty,
                "RequestDate": "2026-08-10T00:00:00", "PromiseDate": "2026-08-12T00:00:00",
                "MustShipDate": must_ship, "ShipDate": None, "Forecast": "No",
                "DateType": "Firm", "Acknowledged": True, "Expedite": False,
                "_ruleCodes": row_rule_codes,
                "_note": "Two releases with different MustShipDate values on the same order."
                         if so_scenario == "correct_quantity_and_price" else "",
            })
            release_seq += 1

    return sales_orders, details, releases, seq


def main():
    quotes = json.loads((MOCK_DATA_DIR / "data" / "mock_quote_data.json").read_text(encoding="utf-8"))
    quote = next(q for q in quotes if q["file"] == QUOTE_FILE)

    # --- AKA / pricing: merge into existing aka_inventory.json ---
    aka_data = json.loads(AKA_PATH.read_text(encoding="utf-8"))
    existing_items_by_pn = {it["data"]["Header"]["Item #"]: it for it in aka_data["items"]}

    new_items = build_aka_contribution(quote)
    added_items, merged_items = 0, 0
    for pn, item in new_items.items():
        if pn in existing_items_by_pn:
            # Should not happen for this new quote's part numbers, but merge
            # safely (append only) rather than clobber if it ever does.
            existing = existing_items_by_pn[pn]
            existing["data"]["akaDetails"].extend(item["data"]["akaDetails"])
            existing["pricingDetails"].extend(item["pricingDetails"])
            merged_items += 1
        else:
            aka_data["items"].append({"data": item["data"], "pricingDetails": item["pricingDetails"]})
            added_items += 1

    AKA_PATH.write_text(json.dumps(aka_data, indent=2), encoding="utf-8")
    print(f"aka_inventory.json: added {added_items} new items, merged into {merged_items} existing items.")

    # --- Sales orders: merge into existing get_sales_orders.json etc. ---
    existing_so = json.loads((SO_DIR / "get_sales_orders.json").read_text(encoding="utf-8"))
    existing_details = json.loads((SO_DIR / "get_sales_order_details.json").read_text(encoding="utf-8"))
    existing_releases = json.loads((SO_DIR / "get_sales_order_releases.json").read_text(encoding="utf-8"))

    max_existing_id = max((r["Id"] for r in existing_so["records"]), default=500000)
    seq_start = max_existing_id - 500000 + 1

    new_so, new_details, new_releases, _ = build_so_contribution(quote, seq_start)

    existing_so["records"].extend(new_so)
    existing_details["records"].extend(new_details)
    existing_releases["records"].extend(new_releases)

    (SO_DIR / "get_sales_orders.json").write_text(json.dumps(existing_so, indent=2), encoding="utf-8")
    (SO_DIR / "get_sales_order_details.json").write_text(json.dumps(existing_details, indent=2), encoding="utf-8")
    (SO_DIR / "get_sales_order_releases.json").write_text(json.dumps(existing_releases, indent=2), encoding="utf-8")

    print(f"sales orders: added {len(new_so)} orders, {len(new_details)} details, {len(new_releases)} releases.")


if __name__ == "__main__":
    main()
