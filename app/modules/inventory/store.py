import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple
from uuid import UUID

from app.core.exceptions import BusinessException
from app.database.aka_resolution_repository import AkaResolutionRepository
from app.database.inventory_repository import InventoryRepository
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.modules.inventory.schemas import (
    AkaDetail,
    AkaHeader,
    AkaSearchData,
    CreateAkaRequest,
    UpdateAkaRequest,
)

logger = logging.getLogger(__name__)

# app/modules/inventory/store.py -> project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TEST_DATA_AKA_INVENTORY = _PROJECT_ROOT / "evco_test_data" / "mock_data" / "aka_inventory.json"

# Key: (customer_number, manufacturing_bom_number) - identity of one AKA
# mapping within an item. akaItem# (the customer's own part number) is a
# mutable attribute of that mapping, not part of its key - see UpdateAkaRequest.
AkaDetailKey = Tuple[str, str]


class InventoryMockStore:
    """In-memory mock store for the EVCO Inventory AKA workflow (Item # + AKA
    details, keyed by customer#/mfg# within each item).

    Every lookup/mutation is also persisted to the aka_records table via
    InventoryRepository, so this data survives a restart - the in-memory
    dicts remain the source of truth for a given process, the DB is the
    durable record of what was returned/created/updated.
    """

    def __init__(
        self, repository: Optional[InventoryRepository] = None,
        resolution_repository: Optional[AkaResolutionRepository] = None,
        processing_repository: Optional[QuoteProcessingRepository] = None,
    ) -> None:
        self._lock = threading.Lock()
        # item_number -> (AkaHeader, {(customer_number, mfg#): AkaDetail})
        self._headers: Dict[str, AkaHeader] = {}
        self._aka_details: Dict[str, Dict[AkaDetailKey, AkaDetail]] = {}
        self._repository = repository or InventoryRepository()
        self._resolution_repository = resolution_repository or AkaResolutionRepository()
        self._processing_repository = processing_repository or QuoteProcessingRepository()

        self._seed_data()

    def _record_exception(self, line_item_id: Optional[UUID], exception_code: str, message: str) -> None:
        """Best-effort exception_logs write for an AKA lookup/update failure. processing_id
        isn't in the AKA request payloads, so it's resolved from line_item_id when available -
        without a line_item_id there's no way to attribute the exception to a quote."""
        if line_item_id is None:
            return
        try:
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
                # No ArInvtId concept is modeled anywhere in this AKA mock (item_number
                # stands in for it) - left None rather than mislabeling item_number as it.
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

    def _persist_aka(self, item_number: str, aka: AkaDetail, status: str, line_item_id: Optional[UUID] = None) -> None:
        try:
            self._repository.upsert_aka_record(
                aka.customer_number, aka.aka_item_number, item_number, aka.aka_description,
                self._headers.get(item_number, AkaHeader(item_number=item_number, rev="", description="")).description,
                "EA", aka.currency, aka.manufacturing_bom_number,
                aka.minimum_selling_qty, aka.selling_multiples_of,
                rev=aka.rev, customer_name=aka.customer_name, ship_to_attn=aka.ship_to_attn, status=status,
                line_item_id=line_item_id,
            )
        except Exception:
            logger.exception("Failed to persist AKA record %s/%s/%s to aka_records",
                              aka.customer_number, aka.manufacturing_bom_number, item_number)

    def _seed_data(self) -> None:
        """Seeds from evco_test_data/mock_data/aka_inventory.json (built from the
        real dummy quote PDFs - every EVCO part number, BOM, and AKA record that
        appears across all 5 test quotes). If that file is unavailable, the
        store simply starts empty and lookups raise 404 (no more dynamic
        placeholder fallback)."""
        if not _TEST_DATA_AKA_INVENTORY.exists():
            logger.warning("Test-data AKA fixture not found at %s - InventoryMockStore starting unseeded.",
                            _TEST_DATA_AKA_INVENTORY)
            return

        try:
            master = json.loads(_TEST_DATA_AKA_INVENTORY.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load %s - InventoryMockStore starting unseeded.", _TEST_DATA_AKA_INVENTORY)
            return

        for item in master.get("items", []):
            header = item["data"]["Header"]
            evco_pn = header["Item #"]
            self._headers[evco_pn] = AkaHeader(item_number=evco_pn, rev=header.get("Rev") or "", description=header.get("Description") or "")

            details: Dict[AkaDetailKey, AkaDetail] = {}
            for aka in item["data"]["akaDetails"]:
                customer_number = aka.get("customer#")
                mfg_number = aka.get("mfg#") or ""
                if not customer_number:
                    continue  # can't key a lookup without a customer number
                details[(customer_number, mfg_number)] = AkaDetail(
                    aka_item_number=aka.get("akaItem#") or "",
                    aka_description=aka.get("akaDescription") or "",
                    rev=aka.get("rev") or "",
                    customer_number=customer_number,
                    currency=aka.get("currency") or "USD",
                    customer_name=aka.get("customername") or "",
                    manufacturing_bom_number=mfg_number,
                    ship_to_attn=aka.get("shipToAttn") or "",
                    minimum_selling_qty=aka.get("minimumSellingQty") or 0,
                    selling_multiples_of=aka.get("sellingMultiplesOf") or 0,
                )
            if details:
                self._aka_details[evco_pn] = details

        logger.info("InventoryMockStore seeded from test data: %d items, %d AKA records.",
                    len(self._headers), sum(len(d) for d in self._aka_details.values()))

    def get_aka(
        self, item_number: str, customer_number: str, manufacturing_bom_number: str,
        line_item_id: Optional[UUID] = None,
    ) -> AkaSearchData:
        """Return the Header + matching AKA detail for (item#, customer#, mfg#), or raise 404 if unseeded."""
        with self._lock:
            header = self._headers.get(item_number)
            detail = self._aka_details.get(item_number, {}).get((customer_number, manufacturing_bom_number))
        if header is None or detail is None:
            self._record_resolution(line_item_id, item_number, customer_number, manufacturing_bom_number,
                                     decision="NOT_FOUND", status="NOT_FOUND")
            message = (
                f"No AKA record found for item_number '{item_number}', "
                f"customer_number '{customer_number}', mfg# '{manufacturing_bom_number}'"
            )
            self._record_exception(line_item_id, "AKA_NOT_FOUND", message)
            raise BusinessException(
                message=message,
                code="AKA_RECORD_NOT_FOUND",
                status_code=404,
                details={
                    "item_number": item_number,
                    "customer_number": customer_number,
                    "mfg#": manufacturing_bom_number,
                },
            )
        self._persist_aka(item_number, detail, status="FETCHED", line_item_id=line_item_id)
        # A plain fetch changes nothing - NO_CHANGE is the honest decision, not a 5th
        # "FOUND" value the plan didn't call for.
        self._record_resolution(line_item_id, item_number, customer_number, manufacturing_bom_number,
                                 decision="NO_CHANGE", status="FETCHED", before=detail, after=detail)
        return AkaSearchData(header=header, aka_details=[detail])

    def create_aka(self, req: CreateAkaRequest) -> Optional[AkaSearchData]:
        """Create a new AKA mapping under an item. Returns None (409) if one
        already exists for this (item#, customer#, mfg#)."""
        details_in = req.create_aka_details
        with self._lock:
            key = (details_in.customer_number, details_in.manufacturing_bom_number)
            existing_for_item = self._aka_details.setdefault(req.item_number, {})
            if key in existing_for_item:
                self._record_resolution(
                    req.line_item_id, req.item_number, details_in.customer_number, details_in.manufacturing_bom_number,
                    decision="NO_CHANGE", status="DUPLICATE",
                    before=existing_for_item[key], after=existing_for_item[key],
                )
                return None  # Record already exists (409)

            header = self._headers.get(req.item_number) or AkaHeader(
                item_number=req.item_number, rev=details_in.rev, description=details_in.aka_description,
            )
            self._headers[req.item_number] = header

            new_detail = AkaDetail(
                aka_item_number=details_in.aka_item_number,
                aka_description=details_in.aka_description,
                rev=details_in.rev,
                customer_number=details_in.customer_number,
                currency=details_in.currency,
                customer_name=details_in.customer_name,
                manufacturing_bom_number=details_in.manufacturing_bom_number,
                ship_to_attn=details_in.ship_to_attn,
                minimum_selling_qty=details_in.minimum_selling_qty,
                selling_multiples_of=details_in.selling_multiples_of,
            )
            existing_for_item[key] = new_detail

        self._persist_aka(req.item_number, new_detail, status="CREATED", line_item_id=req.line_item_id)
        self._record_resolution(
            req.line_item_id, req.item_number, details_in.customer_number, details_in.manufacturing_bom_number,
            decision="CREATED", status="CREATED", after=new_detail,
        )
        return AkaSearchData(header=header, aka_details=[new_detail])

    def update_aka(self, req: UpdateAkaRequest) -> AkaSearchData:
        """Update the AKA mapping identified by (item#, customer#, mfg#), or
        raise 404 if it doesn't exist - no dynamic-fallback creation."""
        updates = req.update_aka_details
        with self._lock:
            key = (req.customer_number, req.manufacturing_bom_number)
            header = self._headers.get(req.item_number)
            existing = self._aka_details.get(req.item_number, {}).get(key)
            if header is None or existing is None:
                self._record_resolution(req.line_item_id, req.item_number, req.customer_number,
                                         req.manufacturing_bom_number, decision="NOT_FOUND", status="NOT_FOUND")
                message = (
                    f"No AKA record found for item_number '{req.item_number}', "
                    f"customer_number '{req.customer_number}', mfg# '{req.manufacturing_bom_number}'"
                )
                self._record_exception(req.line_item_id, "AKA_UPDATE_TARGET_MISSING", message)
                raise BusinessException(
                    message=message,
                    code="AKA_RECORD_NOT_FOUND",
                    status_code=404,
                    details={
                        "item_number": req.item_number,
                        "customer_number": req.customer_number,
                        "mfg#": req.manufacturing_bom_number,
                    },
                )

            before_snapshot = existing
            updated = AkaDetail(
                aka_item_number=updates.aka_item_number if updates.aka_item_number is not None else existing.aka_item_number,
                aka_description=updates.aka_description if updates.aka_description is not None else existing.aka_description,
                rev=updates.rev if updates.rev is not None else existing.rev,
                customer_number=existing.customer_number,
                currency=updates.currency if updates.currency is not None else existing.currency,
                customer_name=existing.customer_name,
                manufacturing_bom_number=existing.manufacturing_bom_number,
                ship_to_attn=updates.ship_to_attn if updates.ship_to_attn is not None else existing.ship_to_attn,
                minimum_selling_qty=updates.minimum_selling_qty if updates.minimum_selling_qty is not None else existing.minimum_selling_qty,
                selling_multiples_of=updates.selling_multiples_of if updates.selling_multiples_of is not None else existing.selling_multiples_of,
            )
            self._aka_details[req.item_number][key] = updated

        self._persist_aka(req.item_number, updated, status="UPDATED", line_item_id=req.line_item_id)
        decision = "NO_CHANGE" if updated.model_dump() == before_snapshot.model_dump() else "UPDATED"
        self._record_resolution(req.line_item_id, req.item_number, req.customer_number, req.manufacturing_bom_number,
                                 decision=decision, status="UPDATED", before=before_snapshot, after=updated)
        return AkaSearchData(header=header, aka_details=[updated])


# Singleton instance for in-memory data persistence across HTTP requests
inventory_store = InventoryMockStore()
