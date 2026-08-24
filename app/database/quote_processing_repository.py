"""Persistence operations for the supplied quote-processing schema."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection
from app.core.exceptions import BusinessException
from app.modules.extraction.parser import QuoteExtractionResponse


class QuoteProcessingRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def start_processing(self, filename: str, scan_file_id: UUID | None = None) -> UUID:
        processing_id, now = uuid4(), datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO quote_processing (processing_id, scan_file_id, filename, received_time,
                   processing_status, current_agent, retry_count, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (processing_id, scan_file_id, filename, now, "IN_PROGRESS", "extraction", 0, now),
            )
        return processing_id

    def get_processing_row(self, processing_id: UUID) -> dict | None:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT processing_id, quote_id, filename, received_time, processing_status,
                   current_agent, retry_count, raw_extracted_json, completed_at,
                   processing_duration_ms, created_at
                   FROM quote_processing WHERE processing_id = %s""",
                (processing_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            assert cursor.description is not None
            columns = [description[0] for description in cursor.description]
            return dict(zip(columns, row))

    def get_line_item_ids(self, processing_id: UUID) -> list[UUID]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT line_item_id FROM quote_line_items WHERE processing_id = %s ORDER BY created_at, line_item_id",
                (processing_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def save_extraction_result(self, processing_id: UUID, file_path: Path,
                               quote: QuoteExtractionResponse, duration_ms: int) -> list[UUID]:
        now = datetime.now(timezone.utc)
        received_time = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        raw_json = json.dumps(quote.model_dump(mode="json"))
        status = "EXCEPTION" if quote.is_exception else "EXTRACTED"
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """UPDATE quote_processing SET quote_id = %s, filename = %s, received_time = %s,
                   processing_status = %s, current_agent = %s, raw_extracted_json = %s::jsonb,
                   completed_at = %s, processing_duration_ms = %s WHERE processing_id = %s""",
                (quote.quote_number, file_path.name, received_time, status, "extraction", raw_json,
                 now, duration_ms, processing_id),
            )
            # Deliberately omits the deferred audit status/count/report fields.
            cursor.execute(
                """INSERT INTO quote_audit (quote_audit_id, processing_id, quote_id, filename,
                   received_time, customer_name, effective_date, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (uuid4(), processing_id, quote.quote_number, file_path.name, received_time,
                 quote.customer_name, self._parse_date(quote.price_effective_date), now),
            )
            if quote.is_exception:
                self._save_exception_logs(
                    cursor=cursor,
                    processing_id=processing_id,
                    exception_codes=quote.exception_codes,
                    exception_reason=quote.exception_reason,
                    created_at=now,
                )
            line_item_ids: list[UUID] = []
            for ordinal, part in enumerate(quote.parts, start=1):
                for tier_index, tier in enumerate(part.pricing_tiers or [None], start=1):
                    line_item_id = uuid4()
                    cursor.execute(
                        """INSERT INTO quote_line_items (line_item_id, processing_id, quote_id, quote_part_id,
                           line_number, evco_part_id, customer_part_number, description, bom_id, mold_number,
                           moq, price, parts, status, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (line_item_id, processing_id, quote.quote_number, f"{ordinal}-{tier_index}",
                         self._as_int(part.line_number, ordinal), part.evco_part_number,
                         part.customer_part_number, part.part_description, part.manufacturing_bom_number,
                         part.mold_number, self._as_int(tier.moq) if tier else None,
                         self._as_decimal(tier.price) if tier else None, self._as_int(part.box_quantity),
                         "EXTRACTED", now),
                    )
                    line_item_ids.append(line_item_id)
        return line_item_ids

    @staticmethod
    def _save_exception_logs(
        cursor, processing_id: UUID, exception_codes: list[str], exception_reason: str | None, created_at: datetime
    ) -> None:
        """Create one exception record per extraction rule code for a quote."""
        codes = exception_codes or ["EXTRACTION_EXCEPTION"]
        reason_sections = (exception_reason or "Quote extraction was excluded from processing.").split(" | ")
        for code in codes:
            matching_sections = [section for section in reason_sections if code in section]
            message = " | ".join(matching_sections) or exception_reason or "Quote extraction was excluded from processing."
            cursor.execute(
                """INSERT INTO exception_logs (
                   exception_id, line_item_id, processing_id, agent_name, tool_name,
                   exception_code, exception_message, retry_attempt, max_retry_count,
                   is_retryable, resolved, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    uuid4(), None, processing_id, "extraction", "PDFExtractor", code, message,
                    0, 2, code == "EXTRACTION_FAILED", False, created_at,
                ),
            )

    def mark_processing_failed(self, processing_id: UUID, reason: str) -> None:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """UPDATE quote_processing SET processing_status = %s, current_agent = %s,
                   retry_count = retry_count + 1, raw_extracted_json = %s::jsonb, completed_at = %s
                   WHERE processing_id = %s""",
                ("FAILED", "extraction", json.dumps({"error": reason}),
                 datetime.now(timezone.utc), processing_id),
            )

    def save_customer_resolution(self, line_item_id: UUID, customer_name: str,
                                 match_status: str, candidates: list[dict]) -> UUID:
        resolution_id = uuid4()
        matched = candidates[0] if match_status == "UNIQUE" and candidates else None
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO customer_resolution_results (customer_resolution_id, line_item_id,
                   customer_name, customer_no, resolution_status, matched_customer, candidate_customers,
                   status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                (resolution_id, line_item_id, customer_name,
                 matched["customer_number"] if matched else None,
                 match_status, matched["customer_name"] if matched else None,
                 json.dumps(candidates), "COMPLETED", datetime.now(timezone.utc)),
            )
        return resolution_id

    def save_unique_customer_resolution_for_processing(
        self, processing_id: UUID, customer_name: str, candidates: list[dict]
    ) -> UUID:
        """Store one quote-level unique customer resolution, linked to its first line item."""
        if len(candidates) != 1:
            raise ValueError("A unique customer resolution requires exactly one candidate")
        matched = candidates[0]
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT line_item_id FROM quote_line_items
                   WHERE processing_id = %s ORDER BY created_at, line_item_id LIMIT 1""",
                (processing_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise BusinessException(
                    message="No line items were found for the processing ID",
                    code="PROCESSING_LINE_ITEMS_NOT_FOUND",
                    status_code=404,
                    details={"processing_id": str(processing_id)},
                )
            resolution_id = uuid4()
            cursor.execute(
                """INSERT INTO customer_resolution_results (customer_resolution_id, line_item_id,
                   customer_name, customer_no, resolution_status, matched_customer, candidate_customers,
                   status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                (resolution_id, row[0], customer_name, matched["customer_number"], "UNIQUE",
                 matched["customer_name"], json.dumps(candidates), "COMPLETED", datetime.now(timezone.utc)),
            )
        return resolution_id

    def save_customer_resolutions_for_processing(
        self, processing_id: UUID, customer_name: str, match_status: str, candidates: list[dict]
    ) -> list[UUID]:
        """Persist one result for each line item belonging to a processing run."""
        matched = candidates[0] if match_status == "UNIQUE" and candidates else None
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT line_item_id FROM quote_line_items WHERE processing_id = %s ORDER BY created_at, line_item_id",
                (processing_id,),
            )
            line_item_ids = [row[0] for row in cursor.fetchall()]
            if not line_item_ids:
                raise BusinessException(
                    message="No line items were found for the processing ID",
                    code="PROCESSING_LINE_ITEMS_NOT_FOUND",
                    status_code=404,
                    details={"processing_id": str(processing_id)},
                )

            resolution_ids: list[UUID] = []
            for line_item_id in line_item_ids:
                resolution_id = uuid4()
                cursor.execute(
                    """INSERT INTO customer_resolution_results (customer_resolution_id, line_item_id,
                       customer_name, customer_no, resolution_status, matched_customer, candidate_customers,
                       status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)""",
                    (resolution_id, line_item_id, customer_name,
                     matched["customer_number"] if matched else None,
                     match_status, matched["customer_name"] if matched else None,
                     json.dumps(candidates), "COMPLETED", datetime.now(timezone.utc)),
                )
                resolution_ids.append(resolution_id)
        return resolution_ids

    def save_audit_customer_number(self, processing_id: UUID, customer_number: str) -> None:
        """Store the uniquely resolved IQMS customer number on the quote audit record."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "UPDATE quote_audit SET customer_no = %s WHERE processing_id = %s",
                (customer_number, processing_id),
            )

    def save_customer_exceptions_for_processing(
        self, processing_id: UUID, exceptions: dict[str, str]
    ) -> list[UUID]:
        """Log one customer-resolution exception per code for a processing run."""
        with self.connection.connect() as db, db.cursor() as cursor:
            exception_ids: list[UUID] = []
            line_status = "REVIEW_REQUIRED" if set(exceptions) == {"EX-006"} else "EXCEPTION"
            cursor.execute(
                "UPDATE quote_line_items SET status = %s WHERE processing_id = %s",
                (line_status, processing_id),
            )
            for code, message in exceptions.items():
                exception_id = uuid4()
                cursor.execute(
                    """INSERT INTO exception_logs (
                       exception_id, line_item_id, processing_id, agent_name, tool_name,
                       exception_code, exception_message, retry_attempt, max_retry_count,
                       is_retryable, resolved, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        exception_id, None, processing_id, "customer_resolution", "IQMSClient",
                        code, message, 0, 0, False, False, datetime.now(timezone.utc),
                    ),
                )
                exception_ids.append(exception_id)
        return exception_ids

    @staticmethod
    def _as_int(value: str | None, default: int | None = None) -> int | None:
        if value is None or not str(value).strip():
            return default
        try:
            return int(re.sub(r"[^0-9-]", "", str(value)))
        except ValueError:
            return default

    @staticmethod
    def _as_decimal(value: str | None) -> Decimal | None:
        if value is None or not str(value).strip():
            return None
        try:
            return Decimal(re.sub(r"[^0-9.-]", "", str(value)))
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
        return None
