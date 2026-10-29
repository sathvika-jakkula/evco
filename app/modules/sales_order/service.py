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
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from app.core.config import settings
from app.database.pricing_history_repository import PricingHistoryRepository
from app.database.sales_order_repository import SalesOrderSyncRepository
from app.database.sales_order_resolution_repository import SalesOrderResolutionRepository
from app.integrations.iqms import IQMSClient
from app.modules.sales_order.schemas import (
    SalesOrderData,
    SalesOrderDetailData,
    SalesOrderLineData,
    SalesOrderReleaseData,
    UpdateSalesOrderDetailsResponse,
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


def _build_default_iqms_client():
    """Live IQMS WebAPI by default. Set IQMS_DATA_SOURCE=fixture to serve the
    dummy fixture data (evco_test_data/mock_data/sales_orders/) instead.

    Return type is intentionally left unannotated: FixtureIQMSClient is a
    duck-typed stand-in (same get_sales_orders/get_sales_order_details/
    get_sales_order_releases method signatures), not a subclass of IQMSClient.
    """
    if settings.IQMS_USE_FIXTURES:
        from evco_test_data.mock_api.fixture_iqms_client import FixtureIQMSClient
        return FixtureIQMSClient()  # <- DUMMY DATA
    return IQMSClient()  # <- LIVE IQMS WebAPI


class SalesOrderService:
    """Looks up sales order / detail / release records directly from IQMS."""

    def __init__(
        self,
        iqms_client: Optional[IQMSClient] = None,
        sync_repository: Optional[SalesOrderSyncRepository] = None,
        pricing_history_repository: Optional[PricingHistoryRepository] = None,
        resolution_repository: Optional[SalesOrderResolutionRepository] = None,
    ) -> None:
        self.iqms_client = iqms_client or _build_default_iqms_client()
        self._data_source = "dummy" if settings.IQMS_USE_FIXTURES else "iqms"
        self.sync_repository = sync_repository or SalesOrderSyncRepository()
        self.pricing_history_repository = pricing_history_repository or PricingHistoryRepository()
        self.resolution_repository = resolution_repository or SalesOrderResolutionRepository()

    def get_sales_orders(self, item_number: str, line_item_id: Optional[UUID] = None) -> List[SalesOrderData]:
        """Fetch the full sales order list from IQMS and filter to item_number here."""
        raw_records = self.iqms_client.get_sales_orders()
        matching = [
            record
            for record in raw_records
            if isinstance(record, dict) and str(record.get("ItemNumber") or "") == item_number
        ]
        orders = [self._to_sales_order(record) for record in matching]
        for order in orders:
            self._persist_order(order, line_item_id)

        if not orders:
            self._record_resolution(line_item_id, None, "No sales orders found for this item",
                                     decision="NOT_FOUND", status="NOT_FOUND")
        else:
            for order in orders:
                self._record_order_resolution(order, line_item_id)
        return orders

    def _record_order_resolution(self, order: SalesOrderData, line_item_id: Optional[UUID]) -> None:
        # Read-only comparison against the currently active quote pricing for this
        # customer/item/quantity - SalesOrderService itself never mutates anything.
        try:
            reference = self.pricing_history_repository.get_active_price_for_quantity(
                order.customer_number, order.item_number, order.total_qty_ordered,
            )
        except Exception:
            reference = None
            logger.exception("Failed to look up reference price for sales order %s", order.sales_order_id)

        if reference is None:
            # No comparable quote price exists at all - not evidence of a mismatch.
            decision, note = "NO_CHANGE", "No comparable active quote price found to compare against."
        elif float(reference["price"]) == order.unit_price:
            decision, note = "NO_CHANGE", f"Order unit price ${order.unit_price} matches current quoted price."
        else:
            decision, note = "MISMATCH", (
                f"Order unit price ${order.unit_price} differs from current quoted price ${reference['price']}."
            )
        self._record_resolution(line_item_id, str(order.sales_order_id), note, decision=decision, status="OBSERVED")

    def _record_resolution(
        self, line_item_id: Optional[UUID], sales_order_id: Optional[str], note: str, decision: str, status: str,
    ) -> None:
        try:
            self.resolution_repository.record_resolution(
                line_item_id=line_item_id, sales_order_id=sales_order_id, sales_order_note=note,
                updates=None, decision=decision, status=status,
            )
        except Exception:
            logger.exception("Failed to record sales order resolution for %s", sales_order_id)

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

    def get_sales_order_releases(
        self, sales_order_detail_id: int, line_item_id: Optional[UUID] = None
    ) -> List[SalesOrderReleaseData]:
        raw_records = self.iqms_client.get_sales_order_releases(sales_order_detail_id)
        releases = [
            self._to_sales_order_release(record) for record in raw_records if isinstance(record, dict)
        ]
        for release in releases:
            self._persist_release(release, line_item_id)
        return releases

    def _persist_order(self, order: SalesOrderData, line_item_id: Optional[UUID] = None) -> None:
        try:
            self.sync_repository.upsert_sales_order(order, source=self._data_source, line_item_id=line_item_id)
        except Exception:
            logger.exception("Failed to persist sales order %s to sales_orders_synced", order.sales_order_id)

    def _persist_release(self, release: SalesOrderReleaseData, line_item_id: Optional[UUID] = None) -> None:
        try:
            self.sync_repository.upsert_release(release, line_item_id=line_item_id)
        except Exception:
            logger.exception("Failed to persist sales order release %s to sales_order_releases_synced", release.release_id)

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

    def get_sales_order_lines(self, item_number: str) -> List[SalesOrderLineData]:
        """Every sales order line for one EVCO part number, all customers and statuses, with
        its detail and releases nested. One list call, then one details call per order and
        one releases call per detail line (IQMS filters details and releases server-side)."""
        orders = [
            self._to_sales_order(record)
            for record in self.iqms_client.get_sales_orders()
            if isinstance(record, dict) and str(record.get("ItemNumber") or "") == item_number
        ]
        lines: List[SalesOrderLineData] = []
        seen_details: Set[int] = set()
        details_by_order: Dict[int, List[Dict[str, Any]]] = {}
        for order in orders:
            if order.sales_order_id not in details_by_order:
                details_by_order[order.sales_order_id] = self.iqms_client.get_sales_order_details(order.sales_order_id)
            for record in details_by_order[order.sales_order_id]:
                if not isinstance(record, dict) or record.get("ArInvtId") != order.ar_invt_id:
                    continue
                detail = self._to_sales_order_detail(record)
                if detail.sales_order_detail_id in seen_details:
                    continue
                seen_details.add(detail.sales_order_detail_id)
                releases = [
                    self._to_sales_order_release(r)
                    for r in self.iqms_client.get_sales_order_releases(detail.sales_order_detail_id)
                    if isinstance(r, dict)
                ]
                lines.append(SalesOrderLineData(
                    sales_order_id=order.sales_order_id,
                    sales_order_detail_id=detail.sales_order_detail_id,
                    ar_invt_id=order.ar_invt_id,
                    order_number=order.order_number,
                    customer_number=order.customer_number,
                    customer_name=order.company,
                    status=order.status,
                    detail=detail,
                    releases=releases,
                ))
        return lines

    def update_sales_order_details(
        self,
        sales_order_detail_id: int,
        unit_price: Optional[float] = None,
        on_hold: Optional[bool] = None,
        ship_hold: Optional[bool] = None,
        line_item_id: Optional[UUID] = None,
    ) -> UpdateSalesOrderDetailsResponse:
        updated_record = self.sync_repository.update_sales_order_detail(
            sales_order_detail_id=sales_order_detail_id,
            unit_price=unit_price,
            on_hold=on_hold,
            ship_hold=ship_hold,
        )

        if updated_record is None:
            from app.core.exceptions import BusinessException
            raise BusinessException(
                message=f"Sales order detail with ID {sales_order_detail_id} not found",
                code="SALES_ORDER_DETAIL_NOT_FOUND",
                status_code=404,
                details={"sales_order_detail_id": sales_order_detail_id},
            )

        return UpdateSalesOrderDetailsResponse(
            sales_order_detail_id=updated_record["sales_order_detail_id"],
            unit_price=updated_record["unit_price"],
            on_hold=updated_record["on_hold"],
            ship_hold=updated_record["ship_hold"],
            updated_at=updated_record["updated_at"],
        )


# Singleton instance, mirroring the other IQMS-backed services in this codebase
sales_order_service = SalesOrderService()
