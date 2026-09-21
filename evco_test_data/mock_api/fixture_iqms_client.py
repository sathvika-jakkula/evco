"""
FixtureIQMSClient: a drop-in stand-in for app.integrations.iqms.IQMSClient that
reads local JSON fixtures instead of making live HTTP calls to IQMS.

It is injected through the constructor parameters that already exist on the
real services (CustomerRepository(iqms_client=...), InventoryService(iqms_client=...))
- the same pattern already proven in evco_mock_data/validate_mock_quotes.py's
_FixtureIQMSClient. IQMSClient itself, and every module that constructs its
own default instance, is never touched.

Sales Order lookups: the real Sales Order GET APIs already exist
(app/modules/sales_order/service.py / SalesOrderService) and their
request/response contract is used as-is, unmodified - this class only
supplies the raw IQMS-shaped data source (mock_data/sales_orders/*.json)
that SalesOrderService already knows how to parse, in place of a live IQMS
connection. See mock_api/persistent_services.py for where the swap to this
dummy client happens (clearly commented, not inside the protected service).

get_aka_inventory_for_customer reshapes evco_test_data/mock_data/aka_inventory.json
(the master AKA+pricing mock-data file, keyed by EVCO Item #, one entry per
customer variant) into the exact raw dict shape the real
IQMSClient.get_aka_inventory_for_customer(ar_custo_id) returns - CustomerNumber/
AKAItemNumber/ItemNumber/AKADescription/ItemDescription/UOM - so
InventoryService._to_aka_record (real, unmodified code) parses it the same way
it would parse a live IQMS response.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

MOCK_DATA_DIR = Path(__file__).resolve().parent.parent / "mock_data"


class FixtureIQMSClient:
    def __init__(self, mock_data_dir: Path = MOCK_DATA_DIR) -> None:
        self._dir = mock_data_dir
        self._customers = json.loads((self._dir / "iqms_customers_lite.json").read_text(encoding="utf-8"))["data"]
        self._ar_custo_id_to_customer_number = {
            rec["Id"]: rec["CustNo"] for rec in self._customers if rec.get("Id") is not None
        }

        sales_orders_dir = self._dir / "sales_orders"
        self._sales_orders = json.loads((sales_orders_dir / "get_sales_orders.json").read_text(encoding="utf-8"))["records"]
        self._sales_order_details = json.loads((sales_orders_dir / "get_sales_order_details.json").read_text(encoding="utf-8"))["records"]
        self._sales_order_releases = json.loads((sales_orders_dir / "get_sales_order_releases.json").read_text(encoding="utf-8"))["records"]

        aka_master = json.loads((self._dir / "aka_inventory.json").read_text(encoding="utf-8"))
        self._aka_by_customer_number: Dict[str, List[Dict[str, Any]]] = {}
        for item in aka_master.get("items", []):
            header = item["data"]["Header"]
            for aka in item["data"]["akaDetails"]:
                customer_number = aka.get("customer#")
                if not customer_number:
                    continue
                self._aka_by_customer_number.setdefault(customer_number, []).append({
                    "CustomerNumber": customer_number,
                    "AKAItemNumber": aka.get("akaItem#", ""),
                    "ItemNumber": header["Item #"],
                    "AKADescription": aka.get("akaDescription", ""),
                    "ItemDescription": header.get("Description", ""),
                    "UOM": "EACH",
                })

    # --- matches IQMSClient.get_customers_lite -----------------------------
    def get_customers_lite(self) -> List[Dict[str, Any]]:
        return self._customers

    # --- matches IQMSClient.get_aka_inventory_for_customer ------------------
    def get_aka_inventory_for_customer(self, ar_custo_id: int) -> List[Dict[str, Any]]:
        customer_number = self._ar_custo_id_to_customer_number.get(ar_custo_id)
        if not customer_number:
            return []
        return self._aka_by_customer_number.get(customer_number, [])

    # --- matches IQMSClient.get_sales_orders --------------------------------
    def get_sales_orders(self) -> List[Dict[str, Any]]:
        # Real IQMS returns the full unfiltered list here too (see
        # SalesOrderService's own docstring) - SalesOrderService filters by
        # ItemNumber itself, so this fixture does the same: return everything.
        return self._sales_orders

    # --- matches IQMSClient.get_sales_order_details -------------------------
    def get_sales_order_details(self, sales_order_id: int) -> List[Dict[str, Any]]:
        return [r for r in self._sales_order_details if r.get("SalesOrderId") == sales_order_id]

    # --- matches IQMSClient.get_sales_order_releases ------------------------
    def get_sales_order_releases(self, sales_order_detail_id: int) -> List[Dict[str, Any]]:
        return [r for r in self._sales_order_releases if r.get("SalesOrderDetailId") == sales_order_detail_id]
