"""
Builds sales-order mock data covering the SAME EVCO part numbers used across
all 5 reused dummy quote PDFs (evco_mock_data/data/mock_quote_data.json) -
data only, no application/service code.

Writes three files under mock_data/sales_orders/, one per existing Sales
Order GET API, using the EXACT raw field names those APIs already return
(read directly from app/modules/sales_order/service.py's
_to_sales_order/_to_sales_order_detail/_to_sales_order_release - not a new
contract):

  get_sales_orders.json          - IQMSClient.get_sales_orders() shape
  get_sales_order_details.json   - IQMSClient.get_sales_order_details(sales_order_id) shape
  get_sales_order_releases.json  - IQMSClient.get_sales_order_releases(sales_order_detail_id) shape

For each row across the 5 PDFs, one sales order is generated whose
TotalQTYOrdered/UnitPrice normally MATCH the quoted MOQ/price (a "correct"
baseline order for that part). A subset of rows - chosen from scenario tags
that already exist in the source data, not invented ad hoc - get a
deliberately mismatched order instead, so the existing SalesOrderService can
be exercised against realistic validation cases:

  MOQ_LOW_BOUNDARY / BOX_QTY_MOQ_NOT_DIVISIBLE -> TotalQTYOrdered below the quoted MOQ
  COMBINED_MULTI_RULE                          -> UnitPrice differs from the quoted price
  the one row per supersedes_prior_quote=true quote already flagged in
    aka_inventory.json's pricingDetails (inactiveDate set)
                                                -> DateTaken predates the quote's
                                                   price_effective_date, at the
                                                   old (pre-change) price
  first row of the first two quotes                (customer_mismatch)
                                                -> CustomerNumber deliberately
                                                   does not match the row's real
                                                   resolved customer

Every record also carries "_scenario"/"_ruleCodes"/"_ruleNames"/"_hasException"/
"_soScenario"/"_note" annotation fields (underscore-prefixed, ignored by any
real consumer), same convention as build_aka_master.py, sourced from
evco_mock_data/data/rule_catalog.py.
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

    sales_orders = []
    details = []
    releases = []

    seq = 1
    for quote in quotes:
        customer = quote["customer"]
        supersedes = bool(quote.get("supersedes_prior_quote"))
        q_rule_codes = quote_level_rules(quote)
        quote_has_exception = any(c.startswith("EX-") for c in q_rule_codes)
        prior_version_example_emitted = False
        customer_mismatch_emitted = False

        for row in quote.get("rows", []):
            pn = row.get("evco_pn")
            tiers = row.get("tiers") or []
            good_tier = next((t for t in tiers if t.get("moq") and t.get("price")), None)
            if not pn or good_tier is None:
                continue  # BLANK_MOQ/BLANK_PRICE rows: no meaningful SO can be built

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
                unit_price = round(quoted_price * 0.9, 2)  # pre-change price
                date_taken = "2026-05-01T00:00:00"  # predates price_effective_date
                so_scenario = "existing_so_before_quote_price_change"
            elif not customer_mismatch_emitted and quote is quotes[0]:
                customer_mismatch_emitted = True
                customer_number, company = "99999", "UNKNOWN CUSTOMER LLC"
                so_scenario = "customer_mismatch"

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
                # Split into two release lines with DIFFERENT MustShipDate values
                # that still SUM to the order's full quantity (a real partial-
                # shipment schedule, not a formula that overshoots the total).
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

    (ROOT / "mock_data" / "sales_orders").mkdir(parents=True, exist_ok=True)
    (ROOT / "mock_data" / "sales_orders" / "get_sales_orders.json").write_text(
        json.dumps({"records": sales_orders}, indent=2), encoding="utf-8")
    (ROOT / "mock_data" / "sales_orders" / "get_sales_order_details.json").write_text(
        json.dumps({"records": details}, indent=2), encoding="utf-8")
    (ROOT / "mock_data" / "sales_orders" / "get_sales_order_releases.json").write_text(
        json.dumps({"records": releases}, indent=2), encoding="utf-8")

    print(f"Wrote {len(sales_orders)} sales orders, {len(details)} details, {len(releases)} releases")
    from collections import Counter
    print(Counter(r["_soScenario"] for r in sales_orders))


if __name__ == "__main__":
    main()
