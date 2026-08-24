"""Live IQMS-backed AKA inventory lookups.

Replaces InventoryMockStore.get_aka as the data source for GET /inventory/get-aka.
The rest of the AKA workflow (search-part, get-bom-candidates, create-aka,
update-aka) is untouched and still served by the in-memory mock.
"""

import logging
from typing import Any, Dict, List, Optional

from app.core.exceptions import BusinessException
from app.integrations.iqms import IQMSClient
from app.modules.customer.repository import CustomerRepository
from app.modules.inventory.schemas import AkaRecordData

logger = logging.getLogger(__name__)

# IQMS's AKAInventoryForCustomer response does not include these fields at
# all - dummy placeholders are used until a real source is identified.
DUMMY_CURRENCY = "USD"
DUMMY_MANUFACTURING_BOM_NUMBER = "N/A"
DUMMY_MOQ = 0
DUMMY_SELLING_MULTIPLES_OF = 1


class InventoryService:
    """Looks up AKA (customer alias) inventory records directly from IQMS."""

    def __init__(
        self,
        iqms_client: Optional[IQMSClient] = None,
        customer_repository: Optional[CustomerRepository] = None,
    ) -> None:
        self.iqms_client = iqms_client or IQMSClient()
        self.customer_repository = customer_repository or CustomerRepository(iqms_client=self.iqms_client)

    def get_aka(
        self, customer_number: str, customer_part_number: str, item_number: str
    ) -> List[AkaRecordData]:
        """Fetch AKA records for a customer from IQMS, filtered to the requested part number."""
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

        ar_custo_id = candidates[0].ar_custo_id
        raw_records = self.iqms_client.get_aka_inventory_for_customer(ar_custo_id)

        matching = [
            record
            for record in raw_records
            if isinstance(record, dict) and str(record.get("ItemNumber") or "") == item_number
        ]

        return [self._to_aka_record(record) for record in matching]

    @staticmethod
    def _to_aka_record(record: Dict[str, Any]) -> AkaRecordData:
        return AkaRecordData(
            customer_number=str(record.get("CustomerNumber") or ""),
            customer_part_number=str(record.get("AKAItemNumber") or ""),
            item_number=str(record.get("ItemNumber") or ""),
            aka_description=record.get("AKADescription") or "N/A",
            item_description=record.get("ItemDescription") or "N/A",
            uom=record.get("UOM") or "EACH",
            currency=DUMMY_CURRENCY,
            manufacturing_bom_number=DUMMY_MANUFACTURING_BOM_NUMBER,
            moq=DUMMY_MOQ,
            selling_multiples_of=DUMMY_SELLING_MULTIPLES_OF,
        )


# Singleton instance, mirroring InventoryMockStore's module-level singleton pattern
inventory_service = InventoryService()
