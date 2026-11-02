"""
Standalone Notification Micro-Service
======================================
Self-contained FastAPI app with the same email notification logic as the main
EVCO backend, plus the client review workbook as an attachment. It does not
import the main app and has no database: quote data for the hardcoded
processing_ids below is stub data taken from the SAMPLE review sheets, so the
workbook can be generated and mailed without the backend.

Usage:
    # 1. Set env vars (or use a .env in this directory)
    export SMTP_HOST="smtp.gmail.com"
    export SMTP_PORT="587"
    export SMTP_USER="your-email@gmail.com"
    export SMTP_PASSWORD="your-app-password"

    # 2. Run
    pip install fastapi uvicorn python-dotenv pydantic openpyxl
    python notification_service.py

    # 3. Test -- no auth token needed
    curl -X POST http://localhost:8100/send-email \
      -H "Content-Type: application/json" \
      -d '{
        "to": ["recipient@example.com"],
        "subject": "EVCO quote review",
        "body": "<p>Quote review workbook attached.</p>",
        "content_type": "text/html",
        "client_report": {
          "processing_ids": ["8b1a9953-c461-4359-9dee-0b8a1b3c1e6f",
                             "3c7e1f20-9a4d-4b8e-8f0a-5d2c6b1e9a77"]
        }
      }'
"""

import logging
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).resolve().parent / ".env")

SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

PORT: int = int(os.getenv("NOTIFICATION_PORT", "8100"))

DEFAULT_REPORT_FILE_NAME = "EVCO_Review_Per_Quote.xlsx"
# When CLIENT_REPORT_SAVE_ENABLED=true, each generated workbook is also saved into this folder.
CLIENT_REPORT_SAVE_ENABLED: bool = os.getenv("CLIENT_REPORT_SAVE_ENABLED", "false").strip().lower() in ("true", "1", "yes")
CLIENT_REPORT_SAVE_DIR: str = os.getenv(
    "CLIENT_REPORT_SAVE_DIR", str(Path.home() / "Documents" / "EVCO_Client_Reports")
)
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

logger = logging.getLogger("notification-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


# ---------------------------------------------------------------------------
# Stub quote data (stands in for the backend's quote summary)
# ---------------------------------------------------------------------------
BLOCKED_BY_IQMS = "Blocked by IQMS"


@dataclass(frozen=True)
class StubRow:
    line_number: int
    evco_part_number: str
    customer_part_number: str
    category: str
    rule: str
    field: str
    current_value: str
    proposed_value: str
    status: str  # "Updated" | "Exception" | "No action"
    comments: str
    blocked: bool = False  # True when the change needs an IQMS write the API cannot make


@dataclass(frozen=True)
class StubQuote:
    quote_number: str
    customer_label: str
    rows: List[StubRow]
    processing_start: Optional[datetime] = None  # blank in the stub
    duration_minutes: Optional[float] = None  # blank in the stub


# Hardcoded processing_ids -> stub quotes. Rows mirror the reference review sheets.
STUB_QUOTES: Dict[UUID, StubQuote] = {
    UUID("8b1a9953-c461-4359-9dee-0b8a1b3c1e6f"): StubQuote(
        quote_number="SAMPLE-Q-001",
        customer_label="CUST-4471 (sample)",
        rows=[
            StubRow(1, "EVCO-10023", "CUST-4471", "Pricing", "VR-016", "Unit price, qty tier 500",
                    "2.50", "2.75", "Updated", "", blocked=True),
            StubRow(1, "EVCO-10023", "CUST-4471", "Pricing", "VR-017", "Quantity, price tier 2",
                    "250", "300", "Updated", "", blocked=True),
            StubRow(1, "EVCO-10023", "CUST-4471", "Inventory", "VR-013", "Minimum sell qty",
                    "Not readable", "100", "Updated", "", blocked=True),
            StubRow(2, "EVCO-20417", "CUST-4471", "Inventory", "VR-009", "AKA-selected MFG #",
                    "Not readable", "MFG-88213/A", "Updated", "", blocked=True),
            StubRow(2, "EVCO-20417", "CUST-4471", "Sales Order", "EX-010", "Manufacturing hold on SO-45871",
                    "Not on hold", "On hold", "Updated", "Release qty 80 below quoted MOQ 100", blocked=True),
            StubRow(2, "EVCO-20417", "CUST-4471", "Sales Order", "EX-011a", "Ship hold on SO-45871 line 2",
                    "Not on hold", "Ship hold", "Exception", "", blocked=True),
        ],
    ),
    UUID("3c7e1f20-9a4d-4b8e-8f0a-5d2c6b1e9a77"): StubQuote(
        quote_number="SAMPLE-Q-002",
        customer_label="CUST-9007 (sample)",
        rows=[
            StubRow(1, "EVCO-30582", "CUST-9007", "Pricing", "BRM-005", "Add missing AKA price break, qty",
                    "No price break", "1.85", "Updated", "", blocked=True),
            StubRow(1, "EVCO-30582", "CUST-9007", "Pricing", "VR-018", "AKA effective date",
                    "Not readable", "2026-11-01", "Updated", "", blocked=True),
            StubRow(1, "EVCO-30582", "CUST-9007", "Pricing", "BR-021", "AKA quantity break of 1 (warning)",
                    "1", "(notice only)", "No action", "Warning email sent; processing continued, no update needed"),
            StubRow(2, "EVCO-30611", "CUST-9007", "Sales Order", "VR-020", "Line unit price on SO-46022 line 2",
                    "(blank)", "4.10", "Updated", "Applicable price source not agreed (EVCO decision)", blocked=True),
            StubRow(2, "EVCO-30611", "CUST-9007", "Inventory", "VR-001", "AKA item number",
                    "CUST-9007-A", "CUST-9007-B", "Updated", "Mismatch with quote customer part number", blocked=True),
        ],
    ),
}


def fallback_quote(processing_id: UUID) -> StubQuote:
    """Placeholder quote for a processing_id that has no stub data, so any ID can be used."""
    return StubQuote(
        quote_number=f"STUB-{str(processing_id)[:8]}",
        customer_label="(stub data)",
        rows=[
            StubRow(1, "STUB-PART", "STUB-CUST", "Pricing", "VR-016", "Unit price, qty tier 500",
                    "2.50", "2.75", "Updated", "Placeholder row: no stub data for this processing_id", blocked=True),
        ],
    )


def load_stub_quote(processing_id: UUID) -> StubQuote:
    """Stub quote for a processing_id. Unknown IDs get placeholder data, so any ID is accepted."""
    return STUB_QUOTES.get(processing_id) or fallback_quote(processing_id)


def display_comment(row: StubRow) -> str:
    """The usual comment, with 'Blocked by IQMS' appended where it applies."""
    if not row.blocked:
        return row.comments
    return f"{row.comments}; {BLOCKED_BY_IQMS}" if row.comments else BLOCKED_BY_IQMS


def count_violations(quote: StubQuote) -> int:
    return sum(1 for r in quote.rows if r.status in ("Updated", "Exception"))


def count_support_actions(quote: StubQuote) -> int:
    return sum(1 for r in quote.rows if r.status == "Exception" and not r.blocked)


# ---------------------------------------------------------------------------
# Client review workbook (same layout as the backend's client review workbook)
# ---------------------------------------------------------------------------
NAVY = "1F3864"
UPDATE_FILL = PatternFill("solid", fgColor="FFF2CC")
NO_ACTION_FILL = PatternFill("solid", fgColor="E2EFDA")
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color=NAVY)
WRAP = Alignment(wrap_text=True, vertical="top")

SUMMARY_HEADERS = [
    "Quote No.", "Link to Worksheet", "Total Lines", "Business Rule Violations",
    "Support Action Required", "Processing Start Date", "Duration (In Minutes)",
]
SUMMARY_WIDTHS = [22, 24, 13, 24, 24, 22, 22]
QUOTE_HEADERS = [
    "Line #", "EVCO part no.", "Customer P/N", "Category", "Business Rule", "Field",
    "Value in IQMS", "Value to be/was applied to IQMS", "Status", "Comments",
]
QUOTE_COLUMN_WIDTHS = [8, 16, 16, 14, 14, 38, 22, 30, 12, 52]
MAX_SHEET_NAME = 31
_ILLEGAL_SHEET_CHARS = re.compile("[" + re.escape("[]:*?/" + chr(92)) + "]")


def _unique_sheet_name(raw: str, used: set) -> str:
    base = _ILLEGAL_SHEET_CHARS.sub("-", raw).strip()[:MAX_SHEET_NAME] or "Quote"
    name, counter = base, 2
    while name in used:
        suffix = f" ({counter})"
        name = base[: MAX_SHEET_NAME - len(suffix)] + suffix
        counter += 1
    return name


def build_workbook(quotes: List[Tuple[UUID, StubQuote]]) -> Tuple[bytes, List[str]]:
    """Summary sheet plus one sheet per quote. Returns (xlsx bytes, sheet names in order)."""
    workbook = Workbook()
    summary = workbook.worksheets[0]
    summary.title = "Summary"

    used = {"Summary"}
    sheet_names = []
    for _, quote in quotes:
        name = _unique_sheet_name(quote.quote_number, used)
        used.add(name)
        sheet_names.append(name)

    _write_summary(summary, quotes, sheet_names)
    for (processing_id, quote), name in zip(quotes, sheet_names):
        _write_quote_sheet(workbook.create_sheet(name), processing_id, quote)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), ["Summary"] + sheet_names


def _style_header(cell) -> None:
    cell.fill = HEADER_FILL
    cell.font = HEADER_FONT
    cell.alignment = Alignment(vertical="center", wrap_text=True)


def _write_summary(sheet, quotes, sheet_names) -> None:
    sheet["A1"] = "EVCO Quote Review: Summary"
    sheet["A1"].font = TITLE_FONT

    header_row = 4
    for column, title in enumerate(SUMMARY_HEADERS, start=1):
        _style_header(sheet.cell(row=header_row, column=column, value=title))

    row = header_row + 1
    for (_, quote), name in zip(quotes, sheet_names):
        sheet.cell(row=row, column=1, value=quote.quote_number)
        link = sheet.cell(row=row, column=2, value=name)
        link.hyperlink = Hyperlink(ref=link.coordinate, location=f"'{name}'!A1")
        link.font = Font(color="0563C1", underline="single")
        sheet.cell(row=row, column=3, value=len(quote.rows))
        sheet.cell(row=row, column=4, value=count_violations(quote))
        sheet.cell(row=row, column=5, value=count_support_actions(quote))
        start = sheet.cell(row=row, column=6, value=quote.processing_start)
        start.number_format = "yyyy-mm-dd hh:mm"
        duration = sheet.cell(row=row, column=7, value=quote.duration_minutes)
        duration.number_format = "0.00"
        row += 1

    sheet.cell(row=row, column=1, value="Total").font = Font(bold=True)
    totals = (
        sum(len(q.rows) for _, q in quotes),
        sum(count_violations(q) for _, q in quotes),
        sum(count_support_actions(q) for _, q in quotes),
    )
    for column, total in zip((3, 4, 5), totals):
        sheet.cell(row=row, column=column, value=total).font = Font(bold=True)
    for column, width in zip("ABCDEFG", SUMMARY_WIDTHS):
        sheet.column_dimensions[column].width = width


def _write_quote_sheet(sheet, processing_id: UUID, quote: StubQuote) -> None:
    sheet["A1"] = "Quote"
    sheet["A1"].font = TITLE_FONT
    sheet["A2"] = f"Customer: {quote.customer_label}   Processing ID: {processing_id}"

    header_row = 4
    for column, title in enumerate(QUOTE_HEADERS, start=1):
        _style_header(sheet.cell(row=header_row, column=column, value=title))

    for offset, item in enumerate(quote.rows, start=1):
        row = header_row + offset
        values = [
            item.line_number, item.evco_part_number, item.customer_part_number, item.category,
            item.rule, item.field, item.current_value, item.proposed_value, item.status,
            display_comment(item),
        ]
        fill = NO_ACTION_FILL if item.status == "No action" else UPDATE_FILL
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.alignment = WRAP
            cell.fill = fill

    for column, width in enumerate(QUOTE_COLUMN_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = f"A{header_row + 1}"
    if quote.rows:
        sheet.auto_filter.ref = (
            f"A{header_row}:{get_column_letter(len(QUOTE_HEADERS))}{header_row + len(quote.rows)}"
        )


def save_workbook_copy(content: bytes, file_name: str) -> Optional[Path]:
    """Save a copy of a generated workbook into CLIENT_REPORT_SAVE_DIR, named with a timestamp.
    A failure to save is logged and never fails the request."""
    try:
        folder = Path(CLIENT_REPORT_SAVE_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = folder / f"{stamp}_{file_name}"
        path.write_bytes(content)
        logger.info("Saved client review workbook to %s", path)
        return path
    except Exception as exc:
        logger.warning("Could not save client review workbook to %s: %s", CLIENT_REPORT_SAVE_DIR, exc)
        return None


# ---------------------------------------------------------------------------
# Schemas  (mirrors app.modules.notification.schemas)
# ---------------------------------------------------------------------------
class ClientReportAttachment(BaseModel):
    processing_ids: List[UUID] = Field(
        ..., min_length=1,
        description="processing_id of each quote to include in the workbook. One sheet per quote, in this order.",
    )
    file_name: str = Field(
        default=DEFAULT_REPORT_FILE_NAME, min_length=1, max_length=120,
        description="File name of the attached workbook",
    )


class SendEmailRequest(BaseModel):
    to: List[str] = Field(..., min_length=1, description="Recipient email address(es)")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content")
    content_type: Literal["text/plain", "text/html"] = Field(
        default="text/plain", description="MIME type of the email body"
    )
    from_email: Optional[str] = Field(
        default=None,
        alias="from",
        description="Sender email. Falls back to SMTP_USER env var if omitted.",
    )
    processing_id: Optional[UUID] = Field(
        default=None, description="processing_id to associate this notification with, if any"
    )
    client_report: Optional[ClientReportAttachment] = Field(
        default=None,
        description="When set, the client review workbook for these processing_ids is attached to the email.",
    )

    model_config = {"populate_by_name": True}


class RecipientResult(BaseModel):
    notification_id: UUID
    recipient: str
    delivery_status: str  # SENT | FAILED
    sent_time: Optional[datetime] = None
    error: Optional[str] = None


class AttachmentResult(BaseModel):
    file_name: str
    content_type: str
    size_bytes: int
    sheets: List[str]
    processing_count: int


# ---------------------------------------------------------------------------
# Core email logic  (mirrors app.modules.notification.service)
# ---------------------------------------------------------------------------
def send_email_smtp(
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    recipients: List[str],
    subject: str,
    body: str,
    content_type: Literal["text/plain", "text/html"] = "text/plain",
    attachment: Optional[Tuple[str, bytes]] = None,
) -> List[RecipientResult]:
    """
    Connect to SMTP, authenticate once, send to each recipient.
    attachment is (file_name, xlsx bytes) and is added to every message.
    Returns per-recipient results.
    """
    results: List[RecipientResult] = []

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(username, password)

            for recipient in recipients:
                msg = EmailMessage()
                msg["From"] = sender
                msg["To"] = recipient
                msg["Subject"] = subject
                msg.set_content(body, subtype=content_type.split("/")[1])
                if attachment is not None:
                    file_name, content = attachment
                    msg.add_attachment(content, maintype="application",
                                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       filename=file_name)

                sent_time = datetime.now(timezone.utc)
                try:
                    server.send_message(msg)
                    results.append(RecipientResult(
                        notification_id=uuid4(),
                        recipient=recipient,
                        delivery_status="SENT",
                        sent_time=sent_time,
                    ))
                    logger.info("[OK] Sent to %s", recipient)
                except Exception as exc:
                    logger.error("[FAIL] Failed sending to %s: %s", recipient, exc)
                    results.append(RecipientResult(
                        notification_id=uuid4(),
                        recipient=recipient,
                        delivery_status="FAILED",
                        sent_time=sent_time,
                        error=str(exc),
                    ))
    except Exception as conn_exc:
        logger.error("SMTP connection/auth failure (%s:%s): %s", host, port, conn_exc)
        sent_time = datetime.now(timezone.utc)
        for recipient in recipients:
            results.append(RecipientResult(
                notification_id=uuid4(),
                recipient=recipient,
                delivery_status="FAILED",
                sent_time=sent_time,
                error=str(conn_exc),
            ))

    return results


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="EVCO Notification Service (Standalone)",
    description="Self-contained notification micro-service for testing and demos.",
    version="1.1.0",
)


@app.get("/health", tags=["Health"])
async def health():
    """Health check."""
    return {"statusCode": 200, "message": "Notification service is healthy", "data": {"status": "healthy"}}


@app.post("/send-email", tags=["Notifications"], status_code=status.HTTP_200_OK)
async def send_email(payload: SendEmailRequest):
    """
    Send an email notification, optionally with the client review workbook attached.

    Uses server-side SMTP credentials (SMTP_USER / SMTP_PASSWORD env vars).
    The caller only needs to provide `to`, `subject`, and `body`.
    Set `content_type` to `text/html` for HTML, or omit it for `text/plain`.
    Optionally override the sender with the `from` / `from_email` field.
    Add `client_report.processing_ids` to attach the workbook built from those quotes.
    The workbook is built before SMTP is contacted; if it fails, nothing is sent.
    """
    sender = payload.from_email or SMTP_USER
    if not sender:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "statusCode": 400,
                "message": "No sender email configured. Set SMTP_USER env var or include 'from' in the request.",
                "data": None,
            },
        )

    if not SMTP_PASSWORD:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "statusCode": 500,
                "message": "SMTP_PASSWORD env var is not configured on this server.",
                "data": None,
            },
        )

    attachment: Optional[Tuple[str, bytes]] = None
    attachments: List[AttachmentResult] = []
    if payload.client_report is not None:
        quotes: List[Tuple[UUID, StubQuote]] = []
        for processing_id in dict.fromkeys(payload.client_report.processing_ids):
            quotes.append((processing_id, load_stub_quote(processing_id)))
        content, sheet_names = build_workbook(quotes)
        if CLIENT_REPORT_SAVE_ENABLED:
            save_workbook_copy(content, payload.client_report.file_name)
        file_name = payload.client_report.file_name
        attachment = (file_name, content)
        attachments.append(AttachmentResult(
            file_name=file_name,
            content_type=XLSX_CONTENT_TYPE,
            size_bytes=len(content),
            sheets=sheet_names,
            processing_count=len(quotes),
        ))

    results = send_email_smtp(
        host=SMTP_HOST,
        port=SMTP_PORT,
        username=SMTP_USER or sender,
        password=SMTP_PASSWORD,
        sender=sender,
        recipients=payload.to,
        subject=payload.subject,
        body=payload.body,
        content_type=payload.content_type,
        attachment=attachment,
    )

    statuses = {r.delivery_status for r in results}
    if statuses == {"SENT"}:
        overall = "SENT"
    elif statuses == {"FAILED"}:
        overall = "FAILED"
    else:
        overall = "PARTIAL"

    return {
        "statusCode": 200,
        "message": "Notification processed",
        "data": {
            "subject": payload.subject,
            "delivery_status": overall,
            "recipients": [r.model_dump(mode="json") for r in results],
            "attachments": [a.model_dump(mode="json") for a in attachments],
        },
    }


@app.get("/config", tags=["Debug"])
async def show_config():
    """Show current SMTP config (password masked)."""
    return {
        "statusCode": 200,
        "message": "Current SMTP configuration",
        "data": {
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_user": SMTP_USER,
            "smtp_password": "***" if SMTP_PASSWORD else "(not set)",
        },
    }


# ---------------------------------------------------------------------------
# Run directly: python notification_service.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting standalone notification service on port %s", PORT)
    logger.info("SMTP config: host=%s, port=%s, user=%s", SMTP_HOST, SMTP_PORT, SMTP_USER)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
