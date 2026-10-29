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
        manufacturing_bom_number: Optional[str] = None,
        line_item_id: Optional[UUID] = None,
    ) -> AkaSearchData:
        """Return the header and the customer's AKA rows for item_number from IQMS.

        Same contract as InventoryMockStore.get_aka, except IQMS cannot tell
        whether the item number itself exists: a customer with no AKA row for
        the item returns the header with empty aka_details (the 206 case).
        manufacturing_bom_number is not checked against IQMS - it is not in the
        WebAPI response, so the returned detail carries it as empty.
        """
        candidates = self.customer_repository.find_candidates(
            customer_name="", customer_number=customer_number
        )
        if not candidates:
            raise BusinessException(
                message="No IQMS customer found for the given customer_number",
                code="CUSTOMER_NOT_FOUND",
                status_code=404,
                details={"customer_number": customer_number},
            )
        customer = candidates[0]

        raw_records = self.iqms_client.get_aka_inventory_for_customer(customer.ar_custo_id)
        matching = [
            record
            for record in raw_records
            if isinstance(record, dict) and str(record.get("ItemNumber") or "") == item_number
        ]

        if not matching:
            message = (
                f"Item '{item_number}' found but no AKA record matches "
                f"customer_number '{customer_number}' in IQMS"
            )
            self._record_resolution(line_item_id, item_number, customer_number, manufacturing_bom_number,
                                    decision="NOT_FOUND", status="AKA_NOT_FOUND")
            self._record_exception(line_item_id, "AKA_NOT_FOUND", message)
            return AkaSearchData(
                header=AkaHeader(item_number=item_number, rev="", description=""),
                aka_details=[],
            )

        header = AkaHeader(
            item_number=item_number,
            rev=str(matching[0].get("Rev") or ""),
            description=str(matching[0].get("ItemDescription") or ""),
        )
        details = [
            self._to_aka_detail(record, customer_number, manufacturing_bom_number, customer.customer_name)
            for record in matching
        ]
        for detail in details:
            self._record_resolution(line_item_id, item_number, customer_number, manufacturing_bom_number,
                                    decision="NO_CHANGE", status="FETCHED", before=detail, after=detail)
        return AkaSearchData(header=header, aka_details=details)

    def get_price_breaks(
        self,
        customer_number: str,
        item_number: str,
        processing_id: Optional[UUID] = None,
        line_item_id: Optional[UUID] = None,
    ) -> List[PriceBreakData]:
        """Price breaks for (customer, item) from IQMS AKAInventoryItemPriceBreaks.

        ArInvtId and ArCustoId are not in the request, so they are taken from the
        customer's AKA record for the item. A customer or item with no price breaks
        returns an empty list (a business outcome, not an error).
        """
        candidates = self.customer_repository.find_candidates(customer_name="", customer_number=customer_number)
        if not candidates:
            return []
        ar_custo_id = candidates[0].ar_custo_id

        aka_records = self.iqms_client.get_aka_inventory_for_customer(ar_custo_id)
        item_record = next(
            (r for r in aka_records if isinstance(r, dict) and str(r.get("ItemNumber") or "") == item_number
             and r.get("ArInvtId")),
            None,
        )
        raw_breaks = (
            self.iqms_client.get_aka_item_price_breaks(int(item_record["ArInvtId"]), ar_custo_id)
            if item_record else []
        )
        breaks = [
            PriceBreakData(price=float(r.get("UnitPrice") or 0), moq=int(r.get("Quantity") or 0),
                           comment=str(r.get("Comment") or ""))
            for r in raw_breaks if isinstance(r, dict)
        ]
        if not breaks:
            message = f"No price breaks found for customer_number '{customer_number}', evco_part_number '{item_number}'"
            self._record_exception_for_processing(processing_id, line_item_id, "PRICE_NOT_FOUND", message)
        return breaks

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
