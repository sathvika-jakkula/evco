"""Live IQMS-backed Sales Order lookups.

IQMS's SalesOrder endpoint's `filters` query parameter does not actually
filter server-side (confirmed by testing: a request with
filters=ArInvtId.eq~207192 still returns rows for other ArInvtId values),
so get_sales_orders fetches the full list and filters by item_number here.
SalesOrderDetails and SalesOrderReleases use plain query params
(salesOrderId / salesOrderDetailId) which DO filter correctly server-side,
so those are passed straight through.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.integrations.iqms import IQMSClient
from app.modules.sales_order.schemas import (
    SalesOrderData,
    SalesOrderDetailData,
    SalesOrderReleaseData,
)

logger = logging.getLogger(__name__)


def _parse_date(value: Any) -> Optional[datetime]:
    """IQMS uses '0001-01-01T00:00:00' as a not-set sentinel; treat it as None."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.year > 1 else None


class SalesOrderService:
    """Looks up sales order / detail / release records directly from IQMS."""

    def __init__(self, iqms_client: Optional[IQMSClient] = None) -> None:
        self.iqms_client = iqms_client or IQMSClient()

    def get_sales_orders(self, item_number: str) -> List[SalesOrderData]:
        """Fetch the full sales order list from IQMS and filter to item_number here."""
        raw_records = self.iqms_client.get_sales_orders()
        matching = [
            record
            for record in raw_records
            if isinstance(record, dict) and str(record.get("ItemNumber") or "") == item_number
        ]
        return [self._to_sales_order(record) for record in matching]

    def get_sales_order_details(self, sales_order_id: int, ar_invt_id: int) -> List[SalesOrderDetailData]:
        """
        Fetch detail (line item) records for one sales order from IQMS (filters
        correctly server-side by sales_order_id), then filter to ar_invt_id here -
        one order can have multiple lines for different items.
        """
        raw_records = self.iqms_client.get_sales_order_details(sales_order_id)
        matching = [
            record
            for record in raw_records
            if isinstance(record, dict) and record.get("ArInvtId") == ar_invt_id
        ]
        return [self._to_sales_order_detail(record) for record in matching]

    def get_sales_order_releases(self, sales_order_detail_id: int) -> List[SalesOrderReleaseData]:
        raw_records = self.iqms_client.get_sales_order_releases(sales_order_detail_id)
        return [
            self._to_sales_order_release(record) for record in raw_records if isinstance(record, dict)
        ]

    @staticmethod
    def _to_sales_order(record: Dict[str, Any]) -> SalesOrderData:
        return SalesOrderData(
            sales_order_id=int(record.get("Id") or 0),
            sales_order_detail_id=int(record.get("OrdDetailId") or 0),
            ar_invt_id=int(record.get("ArInvtId") or 0),
            order_number=str(record.get("OrderNumber") or ""),
            po_number=record.get("PONumber"),
            customer_number=str(record.get("CustomerNumber") or ""),
            company=str(record.get("Company") or ""),
            item_number=str(record.get("ItemNumber") or ""),
            description=record.get("Description"),
            customer_item_number=record.get("CustomerItemNumber"),
            customer_description=record.get("CustomerDescription"),
            status=record.get("Status"),
            rev=record.get("Rev"),
            total_qty_ordered=float(record.get("TotalQTYOrdered") or 0),
            unit_price=float(record.get("UnitPrice") or 0),
            date_taken=_parse_date(record.get("DateTaken")),
            delivery_date=_parse_date(record.get("DeliveryDate")),
        )

    @staticmethod
    def _to_sales_order_detail(record: Dict[str, Any]) -> SalesOrderDetailData:
        return SalesOrderDetailData(
            sales_order_detail_id=int(record.get("Id") or 0),
            sales_order_id=int(record.get("SalesOrderId") or 0),
            ar_invt_id=int(record.get("ArInvtId") or 0),
            blanket_qty=float(record.get("BlanketQty") or 0),
            unit_price=float(record.get("UnitPrice") or 0),
            list_unit_price=float(record.get("ListUnitPrice") or 0),
            uom=str(record.get("UOM") or ""),
            on_hold=bool(record.get("OnHold") or False),
            ship_hold=bool(record.get("ShipHold") or False),
            discount=float(record.get("Discount") or 0),
            containers=record.get("Containers"),
            drop_ship=bool(record.get("DropShip") or False),
            po_info=record.get("POInfo"),
            note=record.get("CUser1"),
        )

    @staticmethod
    def _to_sales_order_release(record: Dict[str, Any]) -> SalesOrderReleaseData:
        return SalesOrderReleaseData(
            release_id=int(record.get("Id") or 0),
            sales_order_detail_id=int(record.get("SalesOrderDetailId") or 0),
            ar_invt_id=int(record.get("ArInvtId") or 0),
            seq=int(record.get("Seq") or 0),
            qty=float(record.get("Qty") or 0),
            original_qty=float(record.get("OriginalQty") or 0),
            shipped_qty=float(record.get("ShippedQty") or 0),
            left_to_ship=float(record.get("LeftToShip") or 0),
            request_date=_parse_date(record.get("RequestDate")),
            promise_date=_parse_date(record.get("PromiseDate")),
            must_ship_date=_parse_date(record.get("MustShipDate")),
            ship_date=_parse_date(record.get("ShipDate")),
            forecast=record.get("Forecast"),
            date_type=record.get("DateType"),
            acknowledged=bool(record.get("Acknowledged") or False),
            expedite=bool(record.get("Expedite") or False),
        )


# Singleton instance, mirroring the other IQMS-backed services in this codebase
sales_order_service = SalesOrderService()
