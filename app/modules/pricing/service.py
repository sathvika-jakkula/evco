"""Mock service for the RPA-driven EVCO IQMS Price Break workflow.

The RPA operates on whatever customer/item context is already open in
IQMS - callers never supply arinvt_id, arCustoId, priceBreakId, or any
other internal ID. Business context (customer number, EVCO part number,
BOM number) stands in for those IDs instead, mirroring the dynamic-fallback
style already used by InventoryMockStore for the AKA workflow.
"""

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, TypedDict

from app.modules.pricing.schemas import (
    AddPriceBreakRequest,
    AddPriceBreakResponseData,
    PriceBreakData,
    UpdatePriceBreakRequest,
    UpdatePriceBreakResponseData,
)


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


def _default_tiers() -> List[PriceBreakRecord]:
    """Dynamic dummy price-break tiers, shared by seeding and fallback generation."""
    return [
        {
            "quantity": 250,
            "unit_price": 0.769,
            "comment": "First tier pricing break",
            "price_date": datetime(2026, 7, 16, 17, 30, 0, tzinfo=timezone.utc),
            "effective_date": datetime(2021, 8, 16, tzinfo=timezone.utc),
            "inactive_date": None,
        },
        {
            "quantity": 500,
            "unit_price": 0.725,
            "comment": "Second tier pricing break",
            "price_date": datetime(2026, 7, 16, 17, 30, 0, tzinfo=timezone.utc),
            "effective_date": datetime(2021, 8, 16, tzinfo=timezone.utc),
            "inactive_date": None,
        },
        {
            "quantity": 1000,
            "unit_price": 0.689,
            "comment": "Third tier pricing break",
            "price_date": datetime(2026, 7, 16, 17, 30, 0, tzinfo=timezone.utc),
            "effective_date": datetime(2021, 8, 16, tzinfo=timezone.utc),
            "inactive_date": None,
        },
        {
            "quantity": 2500,
            "unit_price": 0.612,
            "comment": "Volume tier pricing break",
            "price_date": datetime(2026, 7, 16, 17, 30, 0, tzinfo=timezone.utc),
            "effective_date": datetime(2021, 8, 16, tzinfo=timezone.utc),
            "inactive_date": None,
        },
    ]


class PriceBreakService:
    """In-memory mock for the RPA-driven IQMS Price Break screen."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Seeded price breaks looked up by business context (get-pricebreaks).
        self._price_breaks_by_context: Dict[PriceBreakContextKey, List[PriceBreakRecord]] = {}
        # add/update operate on whatever's "currently open" on the RPA's
        # screen - their request schemas don't carry customer/item/BOM
        # context (yet), so this stays a single flat list.
        self._current_price_breaks: List[PriceBreakRecord] = []
        self._seed_data()

    def _seed_data(self) -> None:
        seeded_tiers = _default_tiers()
        # Same customer/item/BOM as InventoryMockStore's seeded AKA record,
        # so a caller that just resolved this AKA sees consistent data.
        self._price_breaks_by_context[("CUST-98211", "9445626", "BOM-8798")] = seeded_tiers
        self._current_price_breaks = list(seeded_tiers)

    def get_price_breaks(
        self, customer_number: str, evco_part_number: str, bom_number: str
    ) -> List[PriceBreakData]:
        """Return all price breaks for the given customer/item/BOM context."""
        with self._lock:
            key = (customer_number, evco_part_number, bom_number)
            records = self._price_breaks_by_context.get(key)
            if records is None:
                # Dynamic dummy response echoing the requested business key,
                # mirroring InventoryMockStore's fallback behavior - not
                # persisted, same as search_part/get_bom_candidates/get_aka.
                records = _default_tiers()

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
        matches, a new state is synthesized from the request, mirroring the
        dynamic-fallback behavior used elsewhere in this mock module.
        """
        with self._lock:
            record: Optional[PriceBreakRecord] = next(
                (r for r in self._current_price_breaks if r["quantity"] == req.quantity), None
            )
            if record is None:
                record = {
                    "quantity": req.quantity,
                    "unit_price": req.price,
                    "comment": "",
                    "price_date": datetime.now(timezone.utc),
                    "effective_date": req.effective_date,
                    "inactive_date": req.inactive_date,
                }
                self._current_price_breaks.append(record)
            else:
                record["unit_price"] = req.price
                record["effective_date"] = req.effective_date
                record["inactive_date"] = req.inactive_date

            return UpdatePriceBreakResponseData(
                quantity=record["quantity"],
                price=record["unit_price"],
                effective_date=record["effective_date"],
                inactive_date=record["inactive_date"],
            )


# Singleton instance for in-memory data persistence across HTTP requests
price_break_service = PriceBreakService()
