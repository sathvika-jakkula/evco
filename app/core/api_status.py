"""Data-source status for every HTTP operation the backend serves.

LIVE  - the operation reads/writes a real system (IQMS WebAPI, PostgreSQL,
        SMTP, the local folder, or the PDF extraction pipeline).
DUMMY - the operation still answers from dummy/in-memory data. Create and
        update operations are DUMMY with integration="NOT_INTEGRATED": the
        IQMS WebAPI has no matching create/update endpoint, so they are not
        connected to IQMS and must not be presented as live.

Every operation in the OpenAPI schema must have an entry here
(see app/tests/test_api_status_and_client_report.py), so a new endpoint
cannot ship unmarked.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

LIVE = "LIVE"
DUMMY = "DUMMY"

NOT_INTEGRATED = "NOT_INTEGRATED"  # no WebAPI write endpoint exists; not connected to IQMS
WEBAPI_READ = "WEBAPI_READ"        # calls an existing IQMS WebAPI read endpoint
INTERNAL = "INTERNAL"              # PostgreSQL / local folder / SMTP / extraction pipeline
TEST_UTILITY = "TEST_UTILITY"      # resets in-memory dummy stores; not business logic


@dataclass(frozen=True)
class ApiStatus:
    status: str
    integration: str
    source: str
    note: str = ""


_IQMS_SO = "IQMS WebAPI /SalesDistribution/SalesOrder"

API_STATUS: Dict[Tuple[str, str], ApiStatus] = {
    ("GET", "/health"): ApiStatus(LIVE, INTERNAL, "Local process"),
    ("GET", "/server-time"): ApiStatus(LIVE, INTERNAL, "Local process"),
    ("GET", "/api-status"): ApiStatus(LIVE, INTERNAL, "Local process (this register)"),
    ("POST", "/customers/resolve"): ApiStatus(
        LIVE, WEBAPI_READ, "IQMS WebAPI /CRM/CustomerCentral/CustomersLite",
    ),
    ("POST", "/api/extract-quote"): ApiStatus(LIVE, INTERNAL, "PDF extraction pipeline + PostgreSQL"),
    ("POST", "/api/processing-result"): ApiStatus(LIVE, INTERNAL, "PostgreSQL"),
    ("POST", "/inventory/get-aka"): ApiStatus(
        LIVE, WEBAPI_READ, "IQMS WebAPI /Manufacturing/Inventory/AKAInventoryForCustomer",
        note=(
            "Returns only the fields the WebAPI provides. Currency, manufacturing BOM #, MOQ, "
            "selling multiple and mold number are not in the WebAPI and are omitted."
        ),
    ),
    ("POST", "/inventory/create-aka/dummy"): ApiStatus(
        DUMMY, NOT_INTEGRATED, "In-memory dummy store (InventoryMockStore)",
        note="No IQMS WebAPI create-AKA endpoint; not integrated.",
    ),
    ("POST", "/inventory/update-aka/dummy"): ApiStatus(
        DUMMY, NOT_INTEGRATED, "In-memory dummy store (InventoryMockStore)",
        note="No IQMS WebAPI update-AKA endpoint; not integrated.",
    ),
    ("POST", "/inventory/reset/dummy"): ApiStatus(DUMMY, TEST_UTILITY, "In-memory dummy store (InventoryMockStore)"),
    ("POST", "/inventory/get-pricebreaks"): ApiStatus(
        LIVE, WEBAPI_READ, "IQMS WebAPI /Manufacturing/Inventory/AKAInventoryItemPriceBreaks",
        note="ArInvtId and ArCustoId are resolved from the customer's AKA record for the item.",
    ),
    ("POST", "/inventory/get-pricebreaks/dummy"): ApiStatus(
        DUMMY, NOT_INTEGRATED, "In-memory dummy store (PriceBreakService)",
        note="No price-break read endpoint in the IQMS WebAPI client; still dummy.",
    ),
    ("POST", "/inventory/add-pricebreak/dummy"): ApiStatus(
        DUMMY, NOT_INTEGRATED, "In-memory dummy store (PriceBreakService)",
        note="No IQMS WebAPI create-price-break endpoint; not integrated.",
    ),
    ("POST", "/inventory/update-pricebreak/dummy"): ApiStatus(
        DUMMY, NOT_INTEGRATED, "In-memory dummy store (PriceBreakService)",
        note="No IQMS WebAPI update-price-break endpoint; not integrated.",
    ),
    ("POST", "/monitoring/scan"): ApiStatus(LIVE, INTERNAL, "Local quote folder"),
    ("POST", "/sales-orders/get-sales-orders"): ApiStatus(LIVE, WEBAPI_READ, f"{_IQMS_SO}/SalesOrder"),
    ("POST", "/sales-orders/get-sales-order-details"): ApiStatus(
        LIVE, WEBAPI_READ, f"{_IQMS_SO}/SalesOrderDetails",
    ),
    ("POST", "/sales-orders/get-sales-order-releases"): ApiStatus(
        LIVE, WEBAPI_READ, f"{_IQMS_SO}/SalesOrderReleases",
    ),
    ("POST", "/sales-orders/update-sales-order-details/dummy"): ApiStatus(
        DUMMY, NOT_INTEGRATED, "PostgreSQL mirror only (sales_orders_synced)",
        note="No IQMS WebAPI update-sales-order endpoint; changes the local mirror only.",
    ),
    ("POST", "/quote-processing/record-exception"): ApiStatus(LIVE, INTERNAL, "PostgreSQL"),
    ("POST", "/quote-processing/move-quote-file"): ApiStatus(LIVE, INTERNAL, "Local quote folders"),
    ("POST", "/notifications/send-email"): ApiStatus(LIVE, INTERNAL, "SMTP"),
    ("POST", "/reporting/quote-summary"): ApiStatus(LIVE, INTERNAL, "PostgreSQL"),
    ("POST", "/reporting/client-report"): ApiStatus(LIVE, INTERNAL, "PostgreSQL"),
    ("POST", "/quote-processing/record-review-rows"): ApiStatus(LIVE, INTERNAL, "PostgreSQL review_rows"),
    ("POST", "/sales-orders/get-sales-order-lines"): ApiStatus(
        LIVE, WEBAPI_READ, "IQMS WebAPI /SalesDistribution/SalesOrder (SalesOrder, SalesOrderDetails, SalesOrderReleases)",
    ),
    ("POST", "/testing/reset-all/dummy"): ApiStatus(DUMMY, TEST_UTILITY, "In-memory dummy stores"),
}


def lookup(method: str, path: str) -> Optional[ApiStatus]:
    return API_STATUS.get((method.upper(), path))


def marker(status: ApiStatus) -> str:
    return f"[{status.status}]"
