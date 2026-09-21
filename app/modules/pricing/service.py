"""Mock service for the RPA-driven EVCO IQMS Price Break workflow.

The RPA operates on whatever customer/item context is already open in
IQMS - callers never supply arinvt_id, arCustoId, priceBreakId, or any
other internal ID. Business context (customer number, EVCO part number,
BOM number) stands in for those IDs instead, mirroring the dynamic-fallback
style already used by InventoryMockStore for the AKA workflow.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TypedDict
from uuid import UUID

from app.core.exceptions import BusinessException
from app.database.pricing_history_repository import PricingHistoryRepository
from app.database.pricing_resolution_repository import PricingResolutionRepository
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.modules.pricing.schemas import (
    AddPriceBreakRequest,
    AddPriceBreakResponseData,
    PriceBreakData,
    UpdatePriceBreakRequest,
    UpdatePriceBreakResponseData,
)

logger = logging.getLogger(__name__)

# app/modules/pricing/service.py -> project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TEST_DATA_AKA_INVENTORY = _PROJECT_ROOT / "evco_test_data" / "mock_data" / "aka_inventory.json"


def _parse_test_data_date(text: Optional[str]) -> Optional[datetime]:
    """aka_inventory.json stores dates as printed text (e.g. 'July 20, 2026'),
    same format as the quote PDFs' own Price Effective Date - not ISO."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class PriceBreakRecord(TypedDict):
    quantity: int
    unit_price: float
    comment: str
    price_date: datetime
    effective_date: datetime
    inactive_date: Optional[datetime]


# Key: (customer_number, evco_part_number, bom_number) - business context, not an
# internal IQMS ID.
PriceBreakContextKey = Tuple[str, str, str]


class PriceBreakService:
    """In-memory mock for the RPA-driven IQMS Price Break screen.

    add_price_break/update_price_break also persist to the pricing_history
    table (via PricingHistoryRepository) whenever the request supplies the
    optional customer_number/evco_part_number business-key fields - this
    preserves the previous price version instead of overwriting it
    (BR-015/BR-016), which the in-memory dicts alone cannot do across a
    restart. If those optional fields are omitted, behavior is unchanged
    from before (in-memory only, no history recorded).
    """

    def __init__(
        self, pricing_history_repository: Optional[PricingHistoryRepository] = None,
        pricing_resolution_repository: Optional[PricingResolutionRepository] = None,
        processing_repository: Optional[QuoteProcessingRepository] = None,
    ) -> None:
        self._lock = threading.Lock()
        # Seeded price breaks looked up by business context (get-pricebreaks).
        self._price_breaks_by_context: Dict[PriceBreakContextKey, List[PriceBreakRecord]] = {}
        # add/update operate on whatever's "currently open" on the RPA's
        # screen - their request schemas don't carry customer/item/BOM
        # context (yet), so this stays a single flat list.
        self._current_price_breaks: List[PriceBreakRecord] = []
        self._pricing_history_repository = pricing_history_repository or PricingHistoryRepository()
        self._pricing_resolution_repository = pricing_resolution_repository or PricingResolutionRepository()
        self._processing_repository = processing_repository or QuoteProcessingRepository()
        self._seed_data()

    def _record_exception(self, line_item_id: Optional[UUID], processing_id: Optional[UUID],
                           exception_code: str, message: str) -> None:
        """Best-effort exception_logs write for a pricing lookup/update failure.
        Resolves processing_id from line_item_id when the caller didn't supply one directly."""
        try:
            resolved_processing_id = processing_id
            if resolved_processing_id is None and line_item_id is not None:
                resolved_processing_id = self._processing_repository.get_processing_id_for_line_item(line_item_id)
            if resolved_processing_id is None:
                return
            self._processing_repository.record_exception(
                processing_id=resolved_processing_id,
                agent_name="pricing",
                tool_name="price_break_lookup",
                exception_code=exception_code,
                exception_message=message,
                line_item_id=line_item_id,
                is_retryable=False,
            )
        except Exception:
            logger.exception("Failed to record exception_logs entry %s for line_item_id %s",
                              exception_code, line_item_id)

    def _persist_price_change(self, req, quantity: int, price: float, effective_date: datetime, action: str) -> None:
        evco_part_number = getattr(req, "evco_part_number", None)
        customer_number = getattr(req, "customer_number", None)
        if not evco_part_number or not customer_number:
            return  # no business-key context supplied - in-memory only, same as before
        manufacturing_bom_number = getattr(req, "manufacturing_bom_number", None)
        line_item_id = getattr(req, "line_item_id", None)

        # Decision is derived by comparing against whatever was active BEFORE this
        # write, not just the fact that add/update_price_break was called.
        try:
            previously_active = self._pricing_history_repository.get_active_price(
                customer_number, evco_part_number, manufacturing_bom_number, quantity,
            )
        except Exception:
            previously_active = None
            logger.exception("Failed to read prior active price for %s/%s/qty=%s",
                              customer_number, evco_part_number, quantity)

        if previously_active is None:
            decision = "CREATED"
            inactivated_rows: list = []
        elif float(previously_active["price"]) == price:
            decision = "NO_CHANGE"
            inactivated_rows = []
        else:
            decision = "UPDATED"
            inactivated_rows = [str(previously_active["pricing_history_id"])]

        try:
            self._pricing_history_repository.record_price_change(
                customer_number=customer_number,
                evco_part_number=evco_part_number,
                manufacturing_bom_number=manufacturing_bom_number,
                quantity=quantity,
                new_price=price,
                currency=getattr(req, "currency", None) or "USD",
                new_effective_date=effective_date,
                source_quote_number=getattr(req, "source_quote_number", None),
                source_processing_id=getattr(req, "processing_id", None),
                line_item_id=line_item_id,
            )
        except Exception:
            logger.exception("Failed to persist price change for %s/%s/qty=%s to pricing_history",
                              customer_number, evco_part_number, quantity)

        try:
            self._pricing_resolution_repository.record_resolution(
                line_item_id=line_item_id,
                pricing_action=action,
                pricing_before_value=float(previously_active["price"]) if previously_active else None,
                pricing_after_value=price,
                inactivated_price_rows=inactivated_rows,
                decision=decision,
                status=action,
            )
        except Exception:
            logger.exception("Failed to record pricing resolution for %s/%s/qty=%s",
                              customer_number, evco_part_number, quantity)

    def _seed_data(self) -> None:
        """Seeds from evco_test_data/mock_data/aka_inventory.json's pricingDetails
        (built from the real dummy quote PDFs' quantity-break tiers). If that file
        is unavailable, the service starts unseeded and lookups raise 404 - no more
        fabricated dummy prices standing in for real data (same policy as
        InventoryMockStore for the AKA workflow)."""
        if not _TEST_DATA_AKA_INVENTORY.exists():
            logger.warning("Test-data AKA fixture not found at %s - PriceBreakService starting unseeded.",
                            _TEST_DATA_AKA_INVENTORY)
            return

        try:
            master = json.loads(_TEST_DATA_AKA_INVENTORY.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load %s - PriceBreakService starting unseeded.", _TEST_DATA_AKA_INVENTORY)
            return

        for item in master.get("items", []):
            header = item["data"]["Header"]
            evco_pn = header["Item #"]
            aka_details = item["data"]["akaDetails"]
            # Every part in this test data belongs to exactly one customer
            # (verified when the fixture was built - no shared part numbers
            # across customers), so the first AKA entry's customer is correct
            # for every price tier on this item.
            customer_number = aka_details[0].get("customer#") if aka_details else None
            if not customer_number:
                continue

            for tier in item.get("pricingDetails", []):
                bom_number = tier.get("Comment") or ""
                key = (customer_number, evco_pn, bom_number)
                record: PriceBreakRecord = {
                    "quantity": tier["qty"],
                    "unit_price": tier["price"],
                    "comment": tier.get("_note") or "",
                    "price_date": _parse_test_data_date(tier.get("priceDate")) or datetime.now(timezone.utc),
                    "effective_date": _parse_test_data_date(tier.get("effectiveDate")) or datetime.now(timezone.utc),
                    "inactive_date": _parse_test_data_date(tier.get("inactiveDate")),
                }
                self._price_breaks_by_context.setdefault(key, []).append(record)

        if self._price_breaks_by_context:
            # add/update's single flat "currently open" list starts as a copy
            # of one seeded context's tiers, same relationship as before
            # (still just an initial state - add/update overwrite it per call).
            self._current_price_breaks = list(next(iter(self._price_breaks_by_context.values())))

        logger.info("PriceBreakService seeded from test data: %d business-key contexts.",
                    len(self._price_breaks_by_context))

    def get_price_breaks(
        self, customer_number: str, evco_part_number: str, bom_number: str,
        processing_id: Optional[UUID] = None, line_item_id: Optional[UUID] = None,
    ) -> List[PriceBreakData]:
        """Return all price breaks for a seeded customer/item/BOM context, or raise 404 if unseeded."""
        with self._lock:
            key = (customer_number, evco_part_number, bom_number)
            records = self._price_breaks_by_context.get(key)
        if not records:
            message = (
                f"No price breaks found for customer_number '{customer_number}', "
                f"evco_part_number '{evco_part_number}', manufacturing_bom_number '{bom_number}'"
            )
            try:
                self._pricing_resolution_repository.record_resolution(
                    line_item_id=line_item_id, pricing_action="READ",
                    pricing_before_value=None, pricing_after_value=None,
                    inactivated_price_rows=None, decision="NOT_FOUND", status="REVIEW_REQUIRED",
                )
            except Exception:
                logger.exception("Failed to record pricing resolution for %s/%s/%s (not found)",
                                  customer_number, evco_part_number, bom_number)
            self._record_exception(line_item_id, processing_id, "PRICE_NOT_FOUND", message)
            raise BusinessException(
                message=message,
                code="PRICE_BREAKS_NOT_FOUND",
                status_code=404,
                details={
                    "customer_number": customer_number,
                    "evco_part_number": evco_part_number,
                    "manufacturing_bom_number": bom_number,
                },
            )
        try:
            self._pricing_resolution_repository.record_resolution(
                line_item_id=line_item_id, pricing_action="READ",
                pricing_before_value=None, pricing_after_value=None,
                inactivated_price_rows=None, decision="FOUND", status="SUCCESS",
            )
        except Exception:
            logger.exception("Failed to record pricing resolution for %s/%s/%s (found)",
                              customer_number, evco_part_number, bom_number)
        return [
            PriceBreakData(
                unit_price=record["unit_price"],
                quantity=record["quantity"],
                comment=record["comment"],
            )
            for record in records
        ]

    def add_price_break(self, req: AddPriceBreakRequest) -> AddPriceBreakResponseData:
        """Add a new price break tier to the current customer/item context."""
        with self._lock:
            record: PriceBreakRecord = {
                "quantity": req.quantity,
                "unit_price": req.price,
                "comment": "",
                "price_date": datetime.now(timezone.utc),
                "effective_date": req.effective_date,
                "inactive_date": None,
            }
            self._current_price_breaks.append(record)

        self._persist_price_change(req, record["quantity"], record["unit_price"], record["effective_date"], action="ADD")
        return AddPriceBreakResponseData(
            quantity=record["quantity"],
            price=record["unit_price"],
            price_date=record["price_date"],
            effective_date=record["effective_date"],
            inactive_date=record["inactive_date"],
        )

    def update_price_break(self, req: UpdatePriceBreakRequest) -> UpdatePriceBreakResponseData:
        """
        Update the price break tier identified by quantity - the business
        context the RPA uses in place of a database ID. If no existing tier
        matches, this rejects with PRICE_UPDATE_TARGET_MISSING rather than
        silently creating one - callers that want create-or-update should use
        add-pricebreak instead, which already handles "no prior price" as CREATED.
        """
        with self._lock:
            record: Optional[PriceBreakRecord] = next(
                (r for r in self._current_price_breaks if r["quantity"] == req.quantity), None
            )
            if record is None:
                message = f"No existing price break tier found for quantity '{req.quantity}' to update"
                try:
                    self._pricing_resolution_repository.record_resolution(
                        line_item_id=req.line_item_id, pricing_action="UPDATE",
                        pricing_before_value=None, pricing_after_value=req.price,
                        inactivated_price_rows=None, decision="NOT_FOUND", status="REVIEW_REQUIRED",
                    )
                except Exception:
                    logger.exception("Failed to record pricing resolution for update-target-missing at quantity %s",
                                      req.quantity)
                self._record_exception(req.line_item_id, req.processing_id, "PRICE_UPDATE_TARGET_MISSING", message)
                raise BusinessException(
                    message=message,
                    code="PRICE_UPDATE_TARGET_MISSING",
                    status_code=404,
                    details={"quantity": req.quantity},
                )
            record["unit_price"] = req.price
            record["effective_date"] = req.effective_date
            record["inactive_date"] = req.inactive_date

        self._persist_price_change(req, record["quantity"], record["unit_price"], record["effective_date"], action="UPDATE")
        return UpdatePriceBreakResponseData(
            quantity=record["quantity"],
            price=record["unit_price"],
            effective_date=record["effective_date"],
            inactive_date=record["inactive_date"],
        )


# Singleton instance for in-memory data persistence across HTTP requests
price_break_service = PriceBreakService()
