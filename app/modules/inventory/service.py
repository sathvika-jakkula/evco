"""Live IQMS-backed AKA inventory lookups.

Serves POST /inventory/get-aka when IQMS_DATA_SOURCE is not "fixture"
(get_aka_search). create-aka and update-aka have no WebAPI counterpart and
stay on the in-memory dummy store (InventoryMockStore).
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.core.exceptions import BusinessException
from app.database.aka_resolution_repository import AkaResolutionRepository
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.integrations import oracle_iqms
from app.integrations.iqms import IQMSClient
from app.modules.customer.repository import CustomerRepository
from app.modules.inventory.schemas import AkaDetail, AkaHeader, AkaSearchData
from app.modules.pricing.schemas import PriceBreakData

logger = logging.getLogger(__name__)

# AKA fields the WebAPI cannot supply. get_aka_search returns them empty/zero,
# so consumers must treat them as unknown, not as a verified value.
WEBAPI_UNREADABLE_AKA_FIELDS = (
    "currency", "mfg#", "minimumSellingQty", "sellingMultiplesOf", "soItemNumber",
)


class InventoryService:
    """Looks up AKA (customer alias) inventory records directly from IQMS."""

    def __init__(
        self,
        iqms_client: Optional[IQMSClient] = None,
        customer_repository: Optional[CustomerRepository] = None,
        resolution_repository: Optional[AkaResolutionRepository] = None,
        processing_repository: Optional[QuoteProcessingRepository] = None,
    ) -> None:
        self.iqms_client = iqms_client or IQMSClient()
        self.customer_repository = customer_repository or CustomerRepository(iqms_client=self.iqms_client)
        self._resolution_repository = resolution_repository or AkaResolutionRepository()
        self._processing_repository = processing_repository or QuoteProcessingRepository()

    def get_aka_search(
        self,
        item_number: str,
        customer_number: str,
        manufacturing_bom_number: str,
        line_item_id: Optional[UUID] = None,
    ) -> AkaSearchData:
        """AKA records for (part, customer, BOM) from the IQMS Oracle database (read-only)."""
        rows = oracle_iqms.get_aka_rows(item_number, customer_number, manufacturing_bom_number)
        if not rows:
            message = (
                f"No AKA record found for part '{item_number}', customer '{customer_number}', "
                f"BOM '{manufacturing_bom_number}'"
            )
            self._record_resolution(line_item_id, item_number, customer_number, manufacturing_bom_number,
                                    decision="NOT_FOUND", status="AKA_NOT_FOUND")
            self._record_exception(line_item_id, "AKA_NOT_FOUND", message)
            return AkaSearchData(
                header=AkaHeader(item_number=item_number, rev="", description=""),
                aka_details=[],
            )

        first = rows[0]
        header = AkaHeader(
            item_number=item_number,
            rev=str(first.get("item_rev") or ""),
            description=str(first.get("item_descrip") or ""),
        )
        details = [self._oracle_aka_detail(row, customer_number) for row in rows]
        for detail in details:
            self._record_resolution(line_item_id, item_number, customer_number, manufacturing_bom_number,
                                    decision="NO_CHANGE", status="FETCHED", before=detail, after=detail)
        return AkaSearchData(header=header, aka_details=details)

    def get_price_breaks(
        self,
        customer_number: str,
        item_number: str,
        manufacturing_bom_number: str,
        processing_id: Optional[UUID] = None,
        line_item_id: Optional[UUID] = None,
    ) -> List[PriceBreakData]:
        """Price breaks for (part, customer, BOM) from the IQMS Oracle database (read-only).
        No price breaks is a business outcome: an empty list, not an error."""
        rows = oracle_iqms.get_price_break_rows(item_number, customer_number, manufacturing_bom_number)
        breaks = [
            PriceBreakData(
                price=float(row.get("price") or 0),
                moq=int(row.get("qty") or 0),
                comment=str(row.get("comments") or ""),
                effective_date=row.get("effective_date"),
                inactive_date=row.get("inactive_date"),
                price_date=row.get("price_date"),
            )
            for row in rows
        ]
        if not breaks:
            message = f"No price breaks found for customer_number '{customer_number}', evco_part_number '{item_number}'"
            self._record_exception_for_processing(processing_id, line_item_id, "PRICE_NOT_FOUND", message)
        return breaks

    @staticmethod
    def _oracle_aka_detail(row: Dict[str, Any], customer_number: str) -> AkaDetail:
        return AkaDetail(
            aka_item_number=str(row.get("aka_item_no") or ""),
            aka_description=str(row.get("aka_descrip") or ""),
            rev=str(row.get("aka_rev") or ""),
            customer_number=str(row.get("cust_no") or customer_number),
            currency=str(row.get("currency") or ""),
            customer_name=str(row.get("cust_name") or ""),
            manufacturing_bom_number=str(row.get("mfg_no") or ""),
            ship_to_attn="",
            minimum_selling_qty=int(row.get("min_sell_qty") or 0),
            selling_multiples_of=int(row.get("multiple") or 0),
            so_item_number=str(row.get("so_item_info") or ""),
        )

    @staticmethod
    def _to_aka_detail(
        record: Dict[str, Any], customer_number: str, manufacturing_bom_number: str, customer_name: str
    ) -> AkaDetail:
        return AkaDetail(
            aka_item_number=str(record.get("AKAItemNumber") or ""),
            aka_description=str(record.get("AKADescription") or ""),
            rev=str(record.get("AKARev") or ""),
            customer_number=str(record.get("CustomerNumber") or customer_number),
            currency="",
            customer_name=customer_name or "",
            manufacturing_bom_number="",
            ship_to_attn=str(record.get("ShipToAttn") or ""),
            minimum_selling_qty=0,
            selling_multiples_of=0,
            so_item_number="",
        )

    def _record_resolution(
        self, line_item_id: Optional[UUID], item_number: str, customer_number: str,
        manufacturing_bom_number: str, decision: str, status: str,
        before: Optional[AkaDetail] = None, after: Optional[AkaDetail] = None,
    ) -> None:
        reference = after if after is not None else before
        try:
            self._resolution_repository.record_resolution(
                line_item_id=line_item_id,
                aka_id=reference.aka_item_number if reference is not None else None,
                ar_invt_id=None,
                ar_cust_id=customer_number,
                bom_id=manufacturing_bom_number,
                decision=decision,
                status=status,
                before_value=before.model_dump(by_alias=True) if before else None,
                after_value=after.model_dump(by_alias=True) if after else None,
            )
        except Exception:
            logger.exception("Failed to record AKA resolution for %s/%s/%s",
                             item_number, customer_number, manufacturing_bom_number)

    def _record_exception(self, line_item_id: Optional[UUID], exception_code: str, message: str) -> None:
        """Best-effort exception_logs write, attributed to the quote via line_item_id."""
        self._record_exception_for_processing(None, line_item_id, exception_code, message)

    def _record_exception_for_processing(
        self, processing_id: Optional[UUID], line_item_id: Optional[UUID], exception_code: str, message: str,
    ) -> None:
        """Best-effort exception_logs write. processing_id is used when given, else resolved from line_item_id."""
        try:
            if processing_id is None and line_item_id is not None:
                processing_id = self._processing_repository.get_processing_id_for_line_item(line_item_id)
            if processing_id is None:
                return
            self._processing_repository.record_exception(
                processing_id=processing_id,
                agent_name="inventory",
                tool_name="aka_lookup",
                exception_code=exception_code,
                exception_message=message,
                line_item_id=line_item_id,
                is_retryable=False,
            )
        except Exception:
            logger.exception("Failed to record exception_logs entry %s for line_item_id %s",
                             exception_code, line_item_id)



# Singleton instance, mirroring InventoryMockStore's module-level singleton pattern
inventory_service = InventoryService()
