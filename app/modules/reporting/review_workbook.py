"""
Quote validation workbook in the "EVCO Quote Validation Report" layout:
Dashboard, Quotes Requiring Attention, Processing Audit, one sheet per quote, and
Business Rules and Requirements.

Every value comes from the database (quote_processing, quote_audit, quote_line_items,
quote_scans, quote_scan_files, exception_logs, review_rows). Stage results and outcomes are
derived here:

Agent cell (A3 / A4 / A5, per line; review areas AKA / PRICING / SALES_ORDER):
- Not run: the agent has no rows for the line, or an earlier agent stopped the line.
- Fail: the agent has an exception or a Not Validated (NOT_VERIFIABLE) row for the line.
- Pass: otherwise, including Validated and Action Required rows.
A1 and A2 (whole quote): A1 fails when extraction rejected the quote (ExtractionAgent exception);
A2 fails when the customer was not found or was ambiguous (CustomerAgent exception). A1 Fail sets
A2-A5 to Not run; A2 Fail sets A3-A5 to Not run.

Final Outcome per line: "Stopped - A<n>" where A<n> is the first agent with an Action Required,
exception or Not Validated row for the line (agents after it are Not run); otherwise "Pass".
Final Outcome per quote: "Pass" if every line passes, otherwise "Stopped - A<n>" at the earliest
stop (A1/A2 stops first). The Dashboard "Stopped at" counts use this value.
- Quote Validation End: the latest agent timestamp of the run (exception_logs and review_rows),
  falling back to quote_processing.completed_at.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from io import BytesIO
from typing import AbstractSet, Any, Dict, List, Optional, Sequence, Set, Tuple
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.config import settings
from app.database.connection import DatabaseConnection
from app.modules.reporting.client_report import BLOCKED_BY_IQMS, BLOCKED_CODE_MARKER, _readable_comment
from app.modules.reporting.rule_reference import (
    RULE_REFERENCE,
    RULE_REFERENCE_HEADERS,
    RULE_REFERENCE_SHEET,
)

TITLE = "Agentic AI Quote Validation - Process Summary"
STAGES = ("A1", "A2", "A3", "A4", "A5")
LINE_STAGES = ("A3", "A4", "A5")
# Agent names used by runs processed before the standard names were introduced.
LEGACY_AGENT_STAGE = {
    "ExtractionAgent": "A1", "extraction": "A1",
    "CustomerAgent": "A2", "customer_resolution": "A2",
    "InventoryAgent": "A3", "inventory": "A3", "AKAReviewAgent": "A3",
    "PricingAgent": "A4", "pricing": "A4", "PricingReviewAgent": "A4",
    "SalesOrderAgent": "A5", "SalesOrderReviewAgent": "A5",
}
AREA_STAGE = {"AKA": "A3", "PRICING": "A4", "SALES_ORDER": "A5"}
# Review rows that need attention (listed and counted as issues).
FAILING_REVIEW_STATUSES = ("UPDATE_REQUIRED", "NOT_VERIFIABLE")
# Review rows that fail a stage. Action Required (UPDATE_REQUIRED) rows do not: the stage still passes.
STAGE_FAILING_STATUSES = ("NOT_VERIFIABLE",)
# How each review_rows status is shown in the Business Rule Status column.
STATUS_LABELS = {"NO_ACTION": "Validated", "UPDATE_REQUIRED": "Action Required", "NOT_VERIFIABLE": "Not Validated"}
DASHBOARD_SHEET = "Dashboard"
ATTENTION_SHEET = "Quotes Requiring Attention"
AUDIT_SHEET = "Processing Audit"
MAX_SHEET_NAME = 31

HEADER_FILL = PatternFill("solid", fgColor="5947BA")
BAND_FILL = PatternFill("solid", fgColor="F4F2FC")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color="5947BA")
WRAP = Alignment(wrap_text=True, vertical="top")
DATE_FORMAT = "mm-dd-yyyy h:mm AM/PM"
DATE_ONLY_FORMAT = "mm-dd-yyyy"
# Dates written as YYYY-MM-DD inside text (messages, values) are shown as MM-DD-YYYY.
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

QUOTE_HEADERS = [
    "Quote No.", "Type", "Price Effective Date", "Customer Name", "Customer No.", "Line No.",
    "Mold No.", "EVCO Part No.", "Manufacturing / BOM No.", "Customer Part No.", "Description",
    "Box Qty", "MOQ", "Price", "PDF Filename", "Final Outcome",
    "A1 PDF Extraction / Field Validation", "A2 Customer Validation",
    "A3 Evaluate Inventory, BOM & AKA", "A4 Determine Pricing & Confirm Actions",
    "A5 Sales Order Evaluation",
    "Business Rule", "Business Rule Status", "Field", "Value in IQMS", "Value to be applied to IQMS", "Comments",
    "Process ID", "Quote Validation Start", "Quote Validation End",
]
QUOTE_WIDTHS = [14, 10, 18, 30, 12, 9, 12, 16, 22, 18, 36, 10, 10, 10, 34, 14, 18, 18, 22, 26, 22,
                20, 16, 26, 22, 30, 40, 38, 20, 20]

ATTENTION_HEADERS = [
    "Quote ID", "PDF Filename", "Customer", "Last Agent", "Processing Outcome", "Issues",
    "Process ID", "Quote Validation Start", "Quote Validation End",
]
AUDIT_HEADERS = [
    "Quote ID", "PDF Filename", "Customer", "Final Outcome", "Process ID",
    "Quote Validation Start", "Quote Validation End",
]
_ILLEGAL_SHEET_CHARS = re.compile("[" + re.escape("[]:*?/" + chr(92)) + "]")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def _fetch(cursor, sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    cursor.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_runs(processing_ids: Sequence[UUID]) -> List[Dict[str, Any]]:
    """Every value the workbook needs, read with SELECT statements only."""
    ids = [str(pid) for pid in processing_ids]
    with DatabaseConnection().connect() as db, db.cursor() as cursor:
        runs = _fetch(cursor, """
            SELECT qp.processing_id, qp.quote_id, qp.filename, qp.created_at, qp.completed_at,
                   qs.scan_started_at, qa.customer_name, qa.customer_no, qa.effective_date,
                   qp.raw_extracted_json
            FROM quote_processing qp
            LEFT JOIN quote_audit qa ON qa.processing_id = qp.processing_id
            LEFT JOIN quote_scan_files qsf ON qsf.scan_file_id = qp.scan_file_id
            LEFT JOIN quote_scans qs ON qs.scan_id = qsf.scan_id
            WHERE qp.processing_id = ANY(%s::uuid[])
        """, (ids,))
        for run in runs:
            raw = run.pop("raw_extracted_json", None)
            if isinstance(raw, str):
                raw = json.loads(raw) if raw else None
            run["type"] = raw.get("type") if isinstance(raw, dict) else None
        lines = _fetch(cursor, """
            SELECT line_item_id, processing_id, line_number, mold_number, evco_part_id, bom_id,
                   customer_part_number, description, parts, moq, price
            FROM quote_line_items
            WHERE processing_id = ANY(%s::uuid[])
            ORDER BY line_number, created_at
        """, (ids,))
        exceptions = _fetch(cursor, """
            SELECT processing_id, line_item_id, agent_name, exception_code, exception_message, created_at
            FROM exception_logs
            WHERE processing_id = ANY(%s::uuid[])
            ORDER BY created_at
        """, (ids,))
        reviews = _fetch(cursor, """
            SELECT processing_id, line_item_id, line_number, review_area, rule_code, check_name,
                   status, message, field, quote_value, system_value, proposed_value, created_at
            FROM review_rows
            WHERE processing_id = ANY(%s::uuid[])
            ORDER BY line_number, sort_order
        """, (ids,))
    return _assemble(runs, lines, exceptions, reviews)


def _assemble(runs, lines, exceptions, reviews) -> List[Dict[str, Any]]:
    by_run = {r["processing_id"]: dict(r, lines=[], exceptions=[], reviews=[]) for r in runs}
    for key, rows in (("lines", lines), ("exceptions", exceptions), ("reviews", reviews)):
        for row in rows:
            if row["processing_id"] in by_run:
                by_run[row["processing_id"]][key].append(row)
    return list(by_run.values())


def latest_run_per_quote(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One run per quote number: the latest one. Order of first appearance is kept."""
    latest: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for run in runs:
        key = run["quote_id"] or str(run["processing_id"])
        if key not in latest:
            order.append(key)
            latest[key] = run
        elif (run["created_at"] or datetime.min) > (latest[key]["created_at"] or datetime.min):
            latest[key] = run
    return [latest[key] for key in order]


# ---------------------------------------------------------------------------
# Stage and outcome rules
# ---------------------------------------------------------------------------
def evaluate(run: Dict[str, Any]) -> Dict[str, Any]:
    """Adds the stage cells, line and quote outcomes, issues, start and end (rules in the module docstring)."""
    run_stages: Set[str] = set()
    lids = [line["line_item_id"] for line in run["lines"]]
    line_stages: Dict[Any, Set[str]] = {lid: set() for lid in lids}   # Fail: exception or Not Validated row
    line_action: Dict[Any, Set[str]] = {lid: set() for lid in lids}   # has an Action Required row
    line_ran: Dict[Any, Set[str]] = {lid: set() for lid in lids}      # has any row at all

    for exc in run["exceptions"]:
        stage = LEGACY_AGENT_STAGE.get(exc["agent_name"] or "")
        if stage is None:
            continue
        if exc["line_item_id"] is None:
            if stage in ("A1", "A2"):
                run_stages.add(stage)
        elif exc["line_item_id"] in line_stages and stage in LINE_STAGES:
            line_stages[exc["line_item_id"]].add(stage)
            line_ran[exc["line_item_id"]].add(stage)

    for rev in run["reviews"]:
        stage = AREA_STAGE.get(rev["review_area"])
        lid = rev["line_item_id"]
        if not stage or lid not in line_ran:
            continue
        line_ran[lid].add(stage)
        if rev["status"] in STAGE_FAILING_STATUSES:
            line_stages[lid].add(stage)
        elif rev["status"] == "UPDATE_REQUIRED":
            line_action[lid].add(stage)

    if "A1" in run_stages:
        quote_stop = "A1"
    elif "A2" in run_stages:
        quote_stop = "A2"
    else:
        quote_stop = None

    line_stop: Dict[Any, Optional[str]] = {}
    for lid in lids:
        stops = line_stages[lid] | line_action[lid]
        line_stop[lid] = quote_stop or next((s for s in LINE_STAGES if s in stops), None)

    if quote_stop:
        first_stage = quote_stop
    else:
        stopped = [stop for stop in line_stop.values() if stop]
        first_stage = min(stopped, key=STAGES.index) if stopped else None

    # Exception timestamps are naive and review timestamps are timezone-aware; compare them as naive local time.
    timestamps = [row["created_at"].replace(tzinfo=None) for row in run["exceptions"] + run["reviews"]
                  if row["created_at"]]

    run.update({
        "run_stages": run_stages,
        "line_stages": line_stages,
        "line_action": line_action,
        "line_ran": line_ran,
        "line_stop": line_stop,
        "outcome": "Stopped" if first_stage else "Pass",
        "first_stage": first_stage,
        "outcome_text": f"Stopped - {first_stage}" if first_stage else "Pass",
        "last_agents": first_stage or "",
        "issues": len(run["exceptions"]) + sum(
            1 for rev in run["reviews"] if rev["status"] in FAILING_REVIEW_STATUSES),
        "start": run["scan_started_at"] or run["created_at"],
        "end": max(timestamps) if timestamps else run["completed_at"],
    })
    return run


def _run_stage_status(stage: str, run: Dict[str, Any]) -> str:
    """A1 and A2 are checked once per quote. A1 Fail leaves A2 not run."""
    if stage in run["run_stages"]:
        return "Fail"
    if stage == "A2" and run["first_stage"] == "A1":
        return "Not run"
    return "Pass"


def _stage_cell(stage: str, run: Dict[str, Any], lid: Any) -> str:
    """One agent's result for one line: Not run, Fail or Pass."""
    if stage in ("A1", "A2"):
        return _run_stage_status(stage, run)
    if run["first_stage"] in ("A1", "A2"):
        return "Not run"
    stop = run["line_stop"].get(lid)
    if stop and STAGES.index(stage) > STAGES.index(stop):
        return "Not run"   # every agent after the one the line stopped at
    if stage not in run["line_ran"].get(lid, set()):
        return "Not run"   # the agent has no rows for the line
    return "Fail" if stage in run["line_stages"].get(lid, set()) else "Pass"


def _line_outcome(run: Dict[str, Any], lid: Any) -> str:
    stop = run["line_stop"].get(lid)
    return f"Stopped - {stop}" if stop else "Pass"


def _quote_stage_status(stage: str, run: Dict[str, Any]) -> str:
    """Whole-quote result for one agent: Fail if any line fails, Pass if any line passes, else Not run."""
    if stage in ("A1", "A2"):
        return _run_stage_status(stage, run)
    cells = {_stage_cell(stage, run, lid) for lid in run["line_stop"]}
    return "Fail" if "Fail" in cells else "Pass" if "Pass" in cells else "Not run"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def _clean(value: Any) -> Any:
    """Makes a value Excel can store: UUIDs become text, timezone-aware datetimes lose their zone,
    and ISO dates inside text are written as MM-DD-YYYY."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return ISO_DATE_RE.sub(lambda m: f"{m.group(2)}-{m.group(3)}-{m.group(1)}", value)
    return value


def _date_only(value: Any) -> Any:
    """Price Effective Date is a date, not a timestamp."""
    return value.date() if isinstance(value, datetime) else value


# The agents name a field either with its API name (field) or a sentence (check_name). Every variant
# seen in review_rows maps to one display label; keys are lower case.
FIELD_LABELS = {
    "Customer": ("customer", "aka customer"),
    "Customer Part Number": ("customer_part_number", "aka customer part number",
                             "sales order customer part number"),
    "Part Description": ("part_description", "aka description"),
    "Inventory Description": ("inventory description",),
    "Revision": ("rev", "aka revision"),
    "Currency": ("currency", "aka currency"),
    "Manufacturing / BOM Number": ("manufacturing_bom_number", "aka manufacturing/bom", "aka manufacturing/bom number"),
    "Minimum Sell Quantity": ("minimum_selling_qty", "aka_moq", "aka minimum sell quantity",
                              "aka minimum sell quantity (duplicate rule)"),
    "Selling Multiple": ("selling_multiples_of", "aka selling multiple"),
    "Box Quantity": ("box_quantity", "parts_per_box", "parts per box"),
    "Mold Number": ("mold_number", "so_item_number", "aka mold number (so item info)",
                    "mold number on sales order item information"),
    "AKA Record": ("aka_record", "aka record exists", "aka check", "aka review", "aka review note"),
    "EVCO Part Number": ("evco part in inventory",),
    "MOQ vs Box Quantity": ("moq vs box quantity",),
    "Quantity Break of 1": ("quantity break of 1", "quantity break of 1 warning", "quantity break warning"),
    "Open Sales Orders": ("open sales orders", "scope open sales orders", "open releases"),
    "Price Break": ("price_break", "price break for this quote"),
    "Effective Date": ("effective_date", "price break effective date"),
    "Inactive Date": ("inactive_date", "price break inactive date", "previous price inactive date"),
    "Price Date": ("price_date", "price break price date"),
    "Comment": ("comment", "price break comment"),
    "Quantity": ("quantity", "price break quantity"),
    "MOQ": ("moq",),
    "Unit Price": ("price", "price break unit price", "sales order unit price", "sales order price for next release"),
    "Pricing Review": ("pricing check", "pricing review", "pricing review note"),
    "Manufacturing Hold": ("on_hold", "manufacturing hold"),
    "Ship Hold": ("ship_hold", "ship hold"),
    "Release Quantity vs MOQ": ("qty_vs_moq", "release quantity vs moq"),
    "Sales Order Review": ("sales_order check", "sales_order review", "sales_order review note",
                           "sales order review", "sales order agent validation"),
}
_FIELD_LOOKUP = {alias: label for label, aliases in FIELD_LABELS.items() for alias in aliases}
_NO_FIELD = {"", "none", "(none)", "null", "�"}
_UPPER_WORDS = {"aka", "moq", "bom", "so", "evco", "iqms"}
_SMALL_WORDS = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "vs"}


def _field_label(field: Any, check_name: Any) -> str:
    """One display name per field, whichever way the agent named it. Unknown names are title-cased."""
    for raw in (field, check_name):
        text = str(raw or "").strip()
        if text.lower() in _NO_FIELD or not any(ch.isalnum() for ch in text):
            continue
        label = _FIELD_LOOKUP.get(text.lower())
        if label:
            return label
        words = text.replace("_", " ").split()
        return " ".join(
            w.upper() if w.lower() in _UPPER_WORDS
            else w.lower() if i and w.lower() in _SMALL_WORDS
            else w[:1].upper() + w[1:]
            for i, w in enumerate(words))
    return ""


def _comment(rev: Dict[str, Any]) -> str:
    """Same comment rule as the client report: API codes removed, 'Blocked by IQMS' where it applies."""
    text = rev["message"] or ""
    readable = _readable_comment(text)
    if BLOCKED_CODE_MARKER not in text:
        return readable
    return f"{readable}; {BLOCKED_BY_IQMS}" if readable else BLOCKED_BY_IQMS


def quote_rows(run: Dict[str, Any]) -> List[List[Any]]:
    """Rows for one quote sheet: run-level exceptions first, then one block per line item."""
    head = [run["quote_id"], run.get("type"), _date_only(run["effective_date"]),
            run["customer_name"], run["customer_no"]]
    tail = [run["filename"], run["outcome_text"]]
    timing = [run["processing_id"], _clean(run["start"]), _clean(run["end"])]
    rows: List[List[Any]] = []

    for exc in run["exceptions"]:
        if exc["line_item_id"] is not None:
            continue
        stage = LEGACY_AGENT_STAGE.get(exc["agent_name"] or "", "")
        field = {"A1": "Extraction", "A2": "Customer"}.get(stage, "")
        stages = [_quote_stage_status(s, run) for s in STAGES]
        rows.append(head + [None] * 9 + tail + stages
                    + [exc["exception_code"], None, field, None, None, exc["exception_message"]] + timing)

    if run["first_stage"] in ("A1", "A2"):
        # The quote stopped before line items were checked: show only quote-level rows, not the parts.
        return rows

    for line in run["lines"]:
        lid = line["line_item_id"]
        base = head + [line["line_number"], line["mold_number"], line["evco_part_id"], line["bom_id"],
                       line["customer_part_number"], line["description"], line["parts"], line["moq"], line["price"]]
        stages = [_stage_cell(s, run, lid) for s in STAGES]
        tail = [run["filename"], _line_outcome(run, lid)]
        reviews = [rev for rev in run["reviews"] if rev["line_item_id"] == lid]
        line_excs = [exc for exc in run["exceptions"] if exc["line_item_id"] == lid]
        if reviews:
            for rev in reviews:
                rows.append(base + tail + stages + [
                    rev["rule_code"], STATUS_LABELS.get(rev["status"], rev["status"]),
                    _field_label(rev["field"], rev["check_name"]),
                    rev["system_value"], rev["proposed_value"], _comment(rev)] + timing)
        elif line_excs:
            for exc in line_excs:
                rows.append(base + tail + stages + [
                    exc["exception_code"], None, None, None, None, exc["exception_message"]] + timing)
        else:
            rows.append(base + tail + stages + [None] * 6 + timing)
    return rows


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------
# Columns of QUOTE_HEADERS kept on every row when grouping by part: Quote No., Line No. and the
# five review columns. Everything else is shown on the first row of each part only.
GROUP_KEEP_COLUMNS = {0, 5, 21, 22, 23, 24, 25, 26}


def _group_rows_by_part(rows: List[List[Any]]) -> List[List[Any]]:
    """Blanks the repeated part fields on each row after a part's first row, so each part's
    details appear once. Rows for a line are consecutive; run-level rows (no line) are left alone."""
    previous_line = None
    for row in rows:
        line = row[5]
        if line is not None and line == previous_line:
            for index in range(len(row)):
                if index not in GROUP_KEEP_COLUMNS:
                    row[index] = None
        previous_line = line
    return rows


def _title(sheet) -> None:
    sheet["A1"] = TITLE
    sheet["A1"].font = TITLE_FONT


def _header(sheet, row: int, headers: Sequence[str]) -> None:
    for column, text in enumerate(headers, start=1):
        cell = sheet.cell(row=row, column=column, value=text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _write_rows(sheet, headers: Sequence[str], rows: List[List[Any]], widths: Sequence[int]) -> None:
    _header(sheet, 2, headers)
    for offset, values in enumerate(rows, start=3):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=offset, column=column, value=_clean(value))
            cell.alignment = WRAP
            if isinstance(value, datetime):
                cell.number_format = DATE_FORMAT
            elif isinstance(value, date):
                cell.number_format = DATE_ONLY_FORMAT
            if offset % 2 == 0:
                cell.fill = BAND_FILL
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A3"
    if rows:
        sheet.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{len(rows) + 2}"


def _sheet_name(raw: str, used: Set[str]) -> str:
    base = _ILLEGAL_SHEET_CHARS.sub("-", raw).strip()[:MAX_SHEET_NAME] or "Quote"
    name, counter = base, 2
    while name in used:
        suffix = f" ({counter})"
        name = base[: MAX_SHEET_NAME - len(suffix)] + suffix
        counter += 1
    return name


def _dashboard(sheet, runs, start, end) -> None:
    _title(sheet)
    # Each quote counts once, at the stage of its Final Outcome ("Stopped - A<n>").
    stopped_at = {stage: 0 for stage in STAGES}
    for run in runs:
        if run["first_stage"]:
            stopped_at[run["first_stage"]] += 1
    passed = sum(1 for run in runs if run["outcome"] == "Pass")
    summary = [
        ("Process Start", _clean(start)),
        ("Process End", _clean(end)),
        ("Total Quotes Processed", len(runs)),
        ("Valid Quotes / No Action", passed),
        ("Quotes Requiring Attention", len(runs) - passed),
    ]
    for offset, (label, value) in enumerate(summary, start=2):
        sheet.cell(row=offset, column=1, value=label).font = Font(bold=True)
        cell = sheet.cell(row=offset, column=3, value=value)
        if isinstance(value, datetime):
            cell.number_format = DATE_FORMAT
    stops = [
        ("Stopped at A1 PDF Extraction / Field Validation", stopped_at["A1"]),
        ("Stopped at A2 Customer Validation", stopped_at["A2"]),
        ("Stopped at A3 Evaluate Inventory, BOM & AKA", stopped_at["A3"]),
        ("Stopped at A4 Determine Pricing & Confirm Actions", stopped_at["A4"]),
        ("Stopped at A5 Sales Order Evaluation", stopped_at["A5"]),
    ]
    for offset, (label, value) in enumerate(stops, start=8):
        sheet.cell(row=offset, column=2, value=label)
        sheet.cell(row=offset, column=3, value=value)
    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 62
    sheet.column_dimensions["C"].width = 24


def _rules_sheet(sheet) -> None:
    _title(sheet)
    sheet["A2"] = "EVCO Quote Validation: Business Rules and Requirements"
    sheet["A2"].font = Font(bold=True)
    _header(sheet, 3, RULE_REFERENCE_HEADERS)
    for offset, entry in enumerate(RULE_REFERENCE, start=4):
        for column, value in enumerate(entry, start=1):
            sheet.cell(row=offset, column=column, value=value).alignment = WRAP
    for column, width in enumerate([16, 12, 12, 40, 44, 70, 18, 30, 46], start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A4"
    sheet.auto_filter.ref = f"A3:{get_column_letter(len(RULE_REFERENCE_HEADERS))}{len(RULE_REFERENCE) + 3}"


# ---------------------------------------------------------------------------
# Sectioned quote sheet (REVIEW_WORKBOOK_QUOTE_LAYOUT_V2)
# ---------------------------------------------------------------------------
V2_LAST_COL = 14
V2_SECTION_FILL = PatternFill("solid", fgColor="1F3864")
V2_LABEL_FILL = PatternFill("solid", fgColor="D9E1F2")
V2_ACTION_FILL = {
    "Fix in IQMS": PatternFill("solid", fgColor="DDEBF7"),
    "Review only": PatternFill("solid", fgColor="EDEDED"),
    "No action": PatternFill("solid", fgColor="E2EFDA"),
}
V2_STAGE_FILL = {
    "Pass": PatternFill("solid", fgColor="E2EFDA"),
    "Fail": PatternFill("solid", fgColor="F8CBAD"),
    "Not run": PatternFill("solid", fgColor="EDEDED"),
}
V2_STAGE_LABELS = {
    "A1": "A1 PDF Extraction / Field Validation",
    "A2": "A2 Customer Validation",
    "A3": "A3 Inventory, BOM & AKA",
    "A4": "A4 Pricing & Confirm Actions",
    "A5": "A5 Sales Order Evaluation",
}
V2_LINE_HEADERS = [
    "Line", "Mold No.", "EVCO Part", "Mfg / BOM No.", "Customer Part No.", "Description", "MOQ", "Price",
    "A3 Inv/BOM/AKA", "A4 Pricing", "A5 Sales Order", "Issues", "Open",
]
V2_ISSUE_HEADERS = [
    "#", "Line", "Stage", "Rule", "Action Type", "Business Rule Status", "Field", "Value on Quote", "Value in IQMS now",
    "Set IQMS to", "Comments",
]
V2_ISSUE_WIDTHS = [12, 10, 10, 12, 14, 16, 30, 22, 22, 20, 70]
V2_PRICE_FORMAT = '"$"#,##0.00'


def _v2_section(sheet, row: int, text: str) -> None:
    for column in range(1, V2_LAST_COL + 1):
        sheet.cell(row=row, column=column).fill = V2_SECTION_FILL
    cell = sheet.cell(row=row, column=1, value=text)
    cell.font = Font(bold=True, size=12, color="FFFFFF")


def _v2_headers(sheet, row: int, headers: Sequence[str]) -> None:
    for column, text in enumerate(headers, start=1):
        cell = sheet.cell(row=row, column=column, value=text)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _shown(value: Any, placeholder: str) -> Any:
    return placeholder if value in (None, "") else value


def _v2_issues(run: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """One entry per unique issue, in line/stage order, plus the number of duplicates removed.
    Duplicates are the same line, stage and field with the same quote, IQMS and proposed values
    (for example the same minimum-sell check reported under two rule codes)."""
    line_no = {line["line_item_id"]: line["line_number"] for line in run["lines"]}
    raw: List[Dict[str, Any]] = []
    for exc in run["exceptions"]:
        raw.append({
            "line_item_id": exc["line_item_id"], "line": line_no.get(exc["line_item_id"]),
            "stage": LEGACY_AGENT_STAGE.get(exc["agent_name"] or "", ""),
            "rule": exc["exception_code"] or "", "field": "",
            "quote": None, "system": None, "proposed": None, "blank": "—", "status": None,
            "action": "Review only", "what": _readable_comment(exc["exception_message"] or ""),
            "note": "",
        })
    for rev in run["reviews"]:
        if rev["status"] not in FAILING_REVIEW_STATUSES + ("NO_ACTION",):
            continue
        message = rev["message"] or ""
        raw.append({
            "line_item_id": rev["line_item_id"], "line": line_no.get(rev["line_item_id"]),
            "stage": AREA_STAGE.get(rev["review_area"], ""),
            "rule": rev["rule_code"] or "", "field": _field_label(rev["field"], rev["check_name"]),
            "quote": rev["quote_value"], "system": rev["system_value"], "proposed": rev["proposed_value"],
            "blank": "(blank)", "status": rev["status"],
            "action": {"UPDATE_REQUIRED": "Fix in IQMS", "NO_ACTION": "No action"}.get(rev["status"], "Review only"),
            "what": _readable_comment(message),
            "note": BLOCKED_BY_IQMS if BLOCKED_CODE_MARKER in message else "",
        })

    raw.sort(key=lambda i: (i["line"] or 0, STAGES.index(i["stage"]) if i["stage"] in STAGES else 9, i["rule"]))
    unique: List[Dict[str, Any]] = []
    seen: Set[Tuple[Any, ...]] = set()
    for issue in raw:
        norm_field = re.sub(r"\(.*?\)", "", issue["field"]).strip().lower()
        if norm_field:
            key = (issue["line_item_id"], issue["stage"], issue["status"], norm_field,
                   issue["quote"], issue["system"], issue["proposed"])
        else:
            key = (issue["line_item_id"], issue["stage"], issue["rule"], issue["what"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique, len(raw) - len(unique)


def _quote_sheet_v2(sheet, run: Dict[str, Any]) -> None:
    issues, duplicates = _v2_issues(run)
    stopped_early = run["first_stage"] in ("A1", "A2")
    if stopped_early:
        # Quote-level issues only: the parts were never checked, so their issues are not shown.
        issues = [issue for issue in issues if issue["line"] is None]
    # No-action rows are listed for reference but are not counted as issues.
    open_issues = [issue for issue in issues if issue["status"] != "NO_ACTION"]
    line_issue_count: Dict[Any, int] = {}
    for issue in open_issues:
        line_issue_count[issue["line_item_id"]] = line_issue_count.get(issue["line_item_id"], 0) + 1

    sheet["A1"] = f"Quote {run['quote_id']} – {run['customer_name'] or ''}".rstrip(" –")
    sheet["A1"].font = TITLE_FONT
    sheet["A2"] = ("Read top to bottom: A. quote header → B. stage status → C. line items → D. issues & actions "
                   "(the working list).")
    sheet["A2"].font = Font(italic=True, color="57606A")

    # A. Quote header: three rows of label/value pairs.
    _v2_section(sheet, 4, "A. QUOTE HEADER")
    header_pairs = [
        [("Quote No.", run["quote_id"]), ("Customer", run["customer_name"]),
         ("Customer No.", run["customer_no"]), ("Process ID", str(run["processing_id"]))],
        [("Price Effective Date", run["effective_date"]), ("PDF Filename", run["filename"]),
         ("Validation Start", run["start"]), ("Validation End", run["end"])],
        [("Overall Outcome", run["outcome_text"]), ("Unique Issues", len(open_issues)),
         ("Open Issues", len(open_issues)), ("Duplicates Removed", duplicates)],
    ]
    for offset, pairs in enumerate(header_pairs):
        row = 5 + offset
        for index, (label, value) in enumerate(pairs):
            label_cell = sheet.cell(row=row, column=1 + index * 3, value=label)
            label_cell.font = Font(bold=True)
            label_cell.fill = V2_LABEL_FILL
            value_cell = sheet.cell(row=row, column=2 + index * 3, value=_clean(value))
            if isinstance(value, datetime):
                value_cell.number_format = DATE_FORMAT
            elif isinstance(value, date):
                value_cell.number_format = DATE_ONLY_FORMAT

    # B. Stage status.
    _v2_section(sheet, 9, "B. STAGE STATUS (A1–A2 are checked once per quote; A3–A5 are checked per line)")
    stopped_early = run["first_stage"] in ("A1", "A2")
    for index, stage in enumerate(STAGES, start=1):
        status = _quote_stage_status(stage, run)
        label = sheet.cell(row=10, column=index, value=V2_STAGE_LABELS[stage])
        label.font = Font(bold=True)
        label.fill = V2_LABEL_FILL
        label.alignment = WRAP
        cell = sheet.cell(row=11, column=index, value=status)
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.fill = V2_STAGE_FILL["Fail" if status.startswith("Fail") else status if status in V2_STAGE_FILL else "Pass"]

    # C. Line items: one row per quote line.
    _v2_section(sheet, 13, "C. LINE ITEMS (one row per quote line)")
    _v2_headers(sheet, 14, V2_LINE_HEADERS)
    row = 15
    if stopped_early:
        sheet.cell(row=row, column=1, value="Line items not shown – quote stopped at A1/A2.")
    for line in ([] if stopped_early else run["lines"]):
        lid = line["line_item_id"]
        values = [line["line_number"], line["mold_number"], line["evco_part_id"], line["bom_id"],
                  line["customer_part_number"], line["description"], line["moq"], line["price"]]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=_clean(value))
            if column == 8:
                cell.number_format = V2_PRICE_FORMAT
        for column, stage in enumerate(LINE_STAGES, start=9):
            text = _stage_cell(stage, run, lid)
            cell = sheet.cell(row=row, column=column, value=text)
            cell.alignment = Alignment(horizontal="center")
            cell.fill = V2_STAGE_FILL.get(text, V2_STAGE_FILL["Pass"])
        sheet.cell(row=row, column=12, value=line_issue_count.get(lid, 0))
        sheet.cell(row=row, column=13, value=line_issue_count.get(lid, 0))
        row += 1

    # D. Issues & actions: the working list.
    row += 1
    _v2_section(sheet, row, "D. ISSUES & ACTIONS (one row per unique issue – this is the working list)")
    row += 1
    _v2_headers(sheet, row, V2_ISSUE_HEADERS)
    for number, issue in enumerate(issues, start=1):
        row += 1
        values = [
            number, issue["line"], issue["stage"], issue["rule"], issue["action"],
            STATUS_LABELS.get(issue["status"], issue["status"]), issue["field"],
            _shown(issue["quote"], "—"), _shown(issue["system"], issue["blank"]),
            _shown(issue["proposed"], "—"), "; ".join(part for part in (issue["what"], issue["note"]) if part),
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=_clean(value))
            cell.alignment = WRAP
        sheet.cell(row=row, column=5).fill = V2_ACTION_FILL.get(issue["action"], V2_ACTION_FILL["Review only"])

    if issues:
        # Filter buttons on the issues table (section D), the working list.
        sheet.auto_filter.ref = f"A{row - len(issues)}:{get_column_letter(len(V2_ISSUE_HEADERS))}{row}"
    for column, width in enumerate(V2_ISSUE_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width


def build_review_workbook(
    processing_ids: Sequence[UUID], generated_at: datetime, sectioned: Optional[bool] = None,
) -> Tuple[bytes, List[str]]:
    """Builds the workbook for the given processing IDs. Returns (xlsx bytes, sheet names).
    sectioned selects the quote-sheet layout (template 2); when None, REVIEW_WORKBOOK_QUOTE_LAYOUT_V2 decides."""
    use_sectioned = settings.REVIEW_WORKBOOK_QUOTE_LAYOUT_V2 if sectioned is None else sectioned
    runs = [evaluate(run) for run in latest_run_per_quote(load_runs(processing_ids))]

    starts = [run["start"] for run in runs if run["start"]]
    batch_start = min(starts) if starts else None

    workbook = Workbook()
    dashboard = workbook.worksheets[0]
    dashboard.title = DASHBOARD_SHEET
    _title(dashboard)
    _dashboard(dashboard, runs, batch_start, generated_at)

    attention = workbook.create_sheet(ATTENTION_SHEET)
    _title(attention)
    _write_rows(attention, ATTENTION_HEADERS, [
        [r["quote_id"], r["filename"], r["customer_name"], r["last_agents"], r["outcome_text"],
         r["issues"], r["processing_id"], r["start"], r["end"]]
        for r in runs if r["outcome"] != "Pass"
    ], [16, 34, 30, 30, 18, 10, 38, 22, 22])

    audit = workbook.create_sheet(AUDIT_SHEET)
    _title(audit)
    _write_rows(audit, AUDIT_HEADERS, [
        [r["quote_id"], r["filename"], r["customer_name"], r["outcome"], r["processing_id"],
         r["start"], r["end"]]
        for r in runs
    ], [16, 34, 30, 16, 38, 22, 22])

    used = {DASHBOARD_SHEET, ATTENTION_SHEET, AUDIT_SHEET, RULE_REFERENCE_SHEET}
    quote_names = []
    for run in runs:
        name = _sheet_name(run["quote_id"] or "Quote", used)
        used.add(name)
        quote_names.append(name)
        sheet = workbook.create_sheet(name)
        if use_sectioned:
            _quote_sheet_v2(sheet, run)
        else:
            _title(sheet)
            rows = quote_rows(run)
            if settings.REVIEW_WORKBOOK_GROUP_ROWS_BY_PART:
                rows = _group_rows_by_part(rows)
            _write_rows(sheet, QUOTE_HEADERS, rows, QUOTE_WIDTHS)

    rules = workbook.create_sheet(RULE_REFERENCE_SHEET)
    _rules_sheet(rules)

    buffer = BytesIO()
    workbook.save(buffer)
    sheet_names = [DASHBOARD_SHEET, ATTENTION_SHEET, AUDIT_SHEET, *quote_names, RULE_REFERENCE_SHEET]
    return buffer.getvalue(), sheet_names
