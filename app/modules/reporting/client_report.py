"""
Client review report: turns Quote Execution Summary data into the rows of the
per-quote review sheets (one row per field-level finding).

Status
- "Update"    : a change is needed in IQMS.
- "No action" : notice only.

Comment codes
- *_WRITE_GAP : the IQMS WebAPI has no create/update endpoint for this data, so
                the change must be made by hand in the IQMS UI.
- AKA_READ_*  : the IQMS WebAPI does not return this AKA field, so its current
                value is reported as "Not readable" rather than a guessed value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.modules.reporting.schemas import LineItemSummary, QuoteSummaryData

UPDATE = "Update"
NO_ACTION = "No action"
NOT_READABLE = "Not readable"
BLANK = "(blank)"

# AKA field (alias used in stored before/after snapshots) -> (category, rule, field label)
AKA_FIELD_RULES: Dict[str, tuple[str, str, str]] = {
    "akaItem#": ("Inventory", "VR-001", "AKA customer part number"),
    "akaDescription": ("Inventory", "VR-002", "AKA part description"),
    "soItemNumber": ("Inventory", "VR-003", "Mold number"),
    "rev": ("Inventory", "VR-004", "AKA revision level"),
    "currency": ("Inventory", "VR-008", "AKA currency"),
    "mfg#": ("Inventory", "VR-009", "AKA manufacturing number (BOM)"),
    "sellingMultiplesOf": ("Inventory", "VR-012", "AKA selling multiple (box qty)"),
    "minimumSellingQty": ("Inventory", "VR-013", "AKA minimum sell qty"),
}

# Fields the WebAPI cannot return -> the read-gap comment code shown on the row.
AKA_UNREADABLE_READ_CODES: Dict[str, str] = {
    "currency": "AKA_READ_CURRENCY",
    "mfg#": "AKA_READ_MFG",
    "minimumSellingQty": "AKA_READ_MIN_SELL",
    "sellingMultiplesOf": "AKA_READ_BOX_QTY",
    "soItemNumber": "AKA_READ_MOLD",
}

# Exception codes recorded by the backend -> (category, rule, field, current, proposed, comment, status)
EXCEPTION_ROWS: Dict[str, tuple[str, str, str, str, str, str]] = {
    "AKA_NOT_FOUND": ("Inventory", "VR-001", "AKA record", "Not found", "Create AKA in IQMS",
                      "AKA_NOT_FOUND; AKA_WRITE_GAP"),
    "AKA_UPDATE_TARGET_MISSING": ("Inventory", "VR-001", "AKA record", "Not found", "Update AKA in IQMS",
                                  "AKA_UPDATE_TARGET_MISSING; AKA_WRITE_GAP"),
    "PRICE_NOT_FOUND": ("Pricing", "VR-015", "Price breaks", "Not found", "Add price breaks in IQMS",
                        "PRICE_NOT_FOUND; PRICE_BREAK_WRITE_GAP"),
    "PRICE_UPDATE_TARGET_MISSING": ("Pricing", "VR-016", "Price break", "Not found", "Update price break in IQMS",
                                    "PRICE_UPDATE_TARGET_MISSING; PRICE_BREAK_WRITE_GAP"),
    "ITEM_NOT_FOUND": ("Inventory", "", "EVCO part number", "Not found", "Review part number",
                       "ITEM_NOT_FOUND"),
    "CUSTOMER_NOT_FOUND": ("Customer", "EX-005", "Customer", "Not found in IQMS", "Review customer",
                           "CUSTOMER_NOT_FOUND"),
}

_PRICE_AMOUNT = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)")


@dataclass
class ReportRow:
    line_number: Optional[int]
    evco_part_number: str
    customer_part_number: str
    category: str
    rule: str
    field: str
    current_value: str
    proposed_value: str
    status: str
    comments: str


@dataclass
class QuoteReport:
    quote_number: str
    processing_id: str
    customer_name: str
    customer_number: str
    rows: List[ReportRow] = field(default_factory=list)
    quote_level_notes: List[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def update_rows(self) -> int:
        return sum(1 for row in self.rows if row.status == UPDATE)

    @property
    def no_action_rows(self) -> int:
        return sum(1 for row in self.rows if row.status == NO_ACTION)


def build_quote_report(summary: QuoteSummaryData) -> QuoteReport:
    rows: List[ReportRow] = []
    for line in summary.line_items:
        rows.extend(_line_rows(line))
    notes = [
        f"{exc.exception_code}: {exc.message}" if exc.message else exc.exception_code
        for exc in summary.quote_exceptions
    ]
    return QuoteReport(
        quote_number=summary.quote_number or "",
        processing_id=str(summary.processing_id),
        customer_name=summary.customer_name or "",
        customer_number=summary.ar_cust_id or "",
        rows=rows,
        quote_level_notes=notes,
    )


def _line_rows(line: LineItemSummary) -> List[ReportRow]:
    base = {
        "line_number": line.line_number,
        "evco_part_number": line.evco_part_id or "",
        "customer_part_number": line.customer_part_number or "",
    }
    rows: List[ReportRow] = []
    # Comment codes already reported for this line, so an exception does not repeat a decision row.
    reported_codes: Set[str] = set()

    if line.aka.decision == "NOT_FOUND":
        rows.append(_row(base, "Inventory", "VR-001", "AKA record (customer part number)",
                         "Not found", line.customer_part_number or BLANK, UPDATE,
                         "AKA_NOT_FOUND; AKA_WRITE_GAP"))
        reported_codes.add("AKA_NOT_FOUND")
    elif line.aka.decision == "UPDATED":
        rows.extend(_aka_field_rows(base, line.aka.before or {}, line.aka.after or {}))

    rows.extend(_pricing_rows(base, line))
    rows.extend(_sales_order_rows(base, line))

    for exc in line.exception_details:
        code = exc.exception_code
        if code in reported_codes:
            continue
        reported_codes.add(code)
        rows.append(_exception_row(base, code, exc.message))
    return rows


def _aka_field_rows(base: dict, before: Dict[str, Any], after: Dict[str, Any]) -> List[ReportRow]:
    rows = []
    for key, (category, rule, label) in AKA_FIELD_RULES.items():
        before_value = before.get(key)
        after_value = after.get(key)
        if _blank_or_same(before_value, after_value):
            continue
        read_code = AKA_UNREADABLE_READ_CODES.get(key)
        if read_code:
            current = NOT_READABLE
            comment = f"{read_code}; AKA_WRITE_GAP"
        else:
            current = _fmt(before_value)
            comment = "AKA_WRITE_GAP"
        rows.append(_row(base, category, rule, label, current, _fmt(after_value), UPDATE, comment))
    return rows


def _pricing_rows(base: dict, line: LineItemSummary) -> List[ReportRow]:
    rows = []
    line_id = line.line_item_id
    for tier in line.pricing.tiers:
        if tier.created_by_line_item_id == line_id:
            rows.append(_row(base, "Pricing", "VR-017", f"Quantity, price tier {tier.quantity}",
                             "No price break", f"{tier.quantity} @ {_price(tier.current_price)}", UPDATE,
                             "PRICE_BREAK_WRITE_GAP"))
        elif tier.inactivated_by_line_item_id == line_id:
            rows.append(_row(base, "Pricing", "VR-017", f"Quantity, price tier {tier.quantity}",
                             f"Active @ {_price(tier.current_price)}", f"Inactive from {tier.inactive_date or BLANK}",
                             UPDATE, "PRICE_BREAK_WRITE_GAP"))
        elif (
            line.pricing.decision == "UPDATED"
            and tier.is_active
            and tier.previous_price is not None
            and tier.previous_price != tier.current_price
        ):
            rows.append(_row(base, "Pricing", "VR-016", f"Unit price, qty tier {tier.quantity}",
                             _price(tier.previous_price), _price(tier.current_price), UPDATE,
                             "PRICE_BREAK_WRITE_GAP"))
    return rows


def _sales_order_rows(base: dict, line: LineItemSummary) -> List[ReportRow]:
    rows = []
    for order in line.sales_order.orders:
        if order.decision != "MISMATCH":
            continue
        amounts = _PRICE_AMOUNT.findall(order.note or "")
        current = f"${amounts[0]}" if amounts else (order.note or BLANK)
        proposed = f"${amounts[1]}" if len(amounts) > 1 else BLANK
        rows.append(_row(base, "Sales Order", "VR-021", f"Unit price on SO {order.sales_order_id or ''}".strip(),
                         current, proposed, UPDATE, "SO_PRICE_WRITE_GAP"))
    return rows


def _exception_row(base: dict, code: str, message: Optional[str]) -> ReportRow:
    if code in EXCEPTION_ROWS:
        category, rule, label, current, proposed, comment = EXCEPTION_ROWS[code]
        return _row(base, category, rule, label, current, proposed, UPDATE, comment)
    return _row(base, "Other", "", code, "", "Review in IQMS", UPDATE, message or code)


def _row(base: dict, category: str, rule: str, field_label: str, current: str, proposed: str,
         status: str, comments: str) -> ReportRow:
    return ReportRow(
        line_number=base["line_number"],
        evco_part_number=base["evco_part_number"],
        customer_part_number=base["customer_part_number"],
        category=category,
        rule=rule,
        field=field_label,
        current_value=current,
        proposed_value=proposed,
        status=status,
        comments=comments,
    )


def _blank_or_same(before: Any, after: Any) -> bool:
    return _normalise(before) == _normalise(after)


def _normalise(value: Any) -> Any:
    return None if value in (None, "") else value


def _fmt(value: Any) -> str:
    return BLANK if _normalise(value) is None else str(value)


def _price(value: float) -> str:
    """Two decimals minimum, more only when the price needs them ($2.50, $0.769)."""
    text = f"{value:.4f}".rstrip("0")
    decimals = text.split(".")[1] if "." in text else ""
    return text + "0" * (2 - len(decimals)) if len(decimals) < 2 else text
