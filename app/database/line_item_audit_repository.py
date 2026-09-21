"""
Persistence for line_item_audit (one rollup row per quote line, combining the
AKA/pricing/sales-order decisions already recorded elsewhere) and for
finalizing quote_audit's dormant summary columns. Also assembles the raw data
the reporting module needs - this repository does no report formatting, it
only reads/writes rows.

mold_check_result / moq_check_result are never written here - no implemented
check exists anywhere in this codebase for either, so those columns stay
NULL rather than claim a check that didn't happen.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class LineItemAuditRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def record_line_item(
        self,
        line_item_id: UUID,
        quote_id: Optional[str],
        quote_part_id: Optional[str],
        part_number: Optional[str],
        ar_invt_id: Optional[str],
        aka_id: Optional[str],
        pricing_action: Optional[str],
        pricing_before_value: Optional[float],
        pricing_after_value: Optional[float],
        inactivated_price_rows: Optional[list[Any]],
        sales_order_id: Optional[str],
        sales_order_note: Optional[str],
        rule_codes_applied: Optional[list[str]],
        line_status: str,
    ) -> UUID:
        """One row per line_item_id - updates in place if this line was already recorded."""
        now = datetime.now(timezone.utc)
        rows_json = json.dumps(inactivated_price_rows, default=str) if inactivated_price_rows is not None else None
        codes_json = json.dumps(rule_codes_applied, default=str) if rule_codes_applied is not None else None

        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT line_item_audit_id FROM line_item_audit WHERE line_item_id = %s", (line_item_id,))
            existing = cursor.fetchone()

            if existing is not None:
                cursor.execute(
                    """UPDATE line_item_audit SET
                           quote_id = %s, quote_part_id = %s, part_number = %s, ar_invt_id = %s, aka_id = %s,
                           pricing_action = %s, pricing_before_value = %s, pricing_after_value = %s,
                           inactivated_price_rows = %s::jsonb, sales_order_id = %s, sales_order_note = %s,
                           rule_codes_applied = %s::jsonb, line_status = %s, completed_time = %s
                       WHERE line_item_id = %s""",
                    (quote_id, quote_part_id, part_number, ar_invt_id, aka_id, pricing_action,
                     pricing_before_value, pricing_after_value, rows_json, sales_order_id, sales_order_note,
                     codes_json, line_status, now, line_item_id),
                )
                return existing[0]

            line_item_audit_id = uuid4()
            cursor.execute(
                """INSERT INTO line_item_audit
                   (line_item_audit_id, line_item_id, quote_id, quote_part_id, part_number, ar_invt_id, aka_id,
                    pricing_action, pricing_before_value, pricing_after_value, inactivated_price_rows,
                    sales_order_id, sales_order_note, rule_codes_applied, line_status, completed_time)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s)""",
                (line_item_audit_id, line_item_id, quote_id, quote_part_id, part_number, ar_invt_id, aka_id,
                 pricing_action, pricing_before_value, pricing_after_value, rows_json,
                 sales_order_id, sales_order_note, codes_json, line_status, now),
            )
            return line_item_audit_id

    def finalize_quote_audit(self, processing_id: UUID, report_id: str) -> None:
        """Rolls up real counts from quote_line_items/exception_logs/line_item_audit into
        quote_audit's dormant summary columns - no logic invented, just aggregation of
        what's already been recorded."""
        now = datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT line_item_id FROM quote_line_items WHERE processing_id = %s", (processing_id,))
            line_item_ids = [row[0] for row in cursor.fetchall()]
            total_lines = len(line_item_ids)

            cursor.execute(
                "SELECT COUNT(DISTINCT line_item_id) FROM exception_logs WHERE processing_id = %s AND line_item_id IS NOT NULL",
                (processing_id,),
            )
            exception_count_row = cursor.fetchone()
            assert exception_count_row is not None, "COUNT(*) always yields exactly one row"
            exception_count = exception_count_row[0]

            successful_lines = review_required_lines = failed_lines = 0
            if line_item_ids:
                cursor.execute(
                    "SELECT line_status, COUNT(*) FROM line_item_audit WHERE line_item_id = ANY(%s) GROUP BY line_status",
                    (line_item_ids,),
                )
                for line_status, count in cursor.fetchall():
                    if line_status == "SUCCESS":
                        successful_lines = count
                    elif line_status == "REVIEW_REQUIRED":
                        review_required_lines = count
                    elif line_status == "FAILED":
                        failed_lines = count

            cursor.execute(
                """UPDATE quote_audit SET
                       total_lines = %s, successful_lines = %s, review_required_lines = %s,
                       failed_lines = %s, exception_count = %s, report_id = %s,
                       completed_time = %s, status = 'COMPLETED'
                   WHERE processing_id = %s""",
                (total_lines, successful_lines, review_required_lines, failed_lines,
                 exception_count, report_id, now, processing_id),
            )

    def get_report_data(self, processing_id: UUID) -> dict[str, Any]:
        """Assembles everything the reporting module needs - header, per-line rollups,
        and each line's pricing/sales-order facts. Returns raw dicts; app/modules/reporting
        maps this into its own response schema, no formatting happens here."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT qp.processing_id, qp.quote_id, qa.customer_name, qa.customer_no, qa.effective_date,
                          qa.report_id, qa.total_lines, qa.successful_lines, qa.review_required_lines,
                          qa.failed_lines, qa.exception_count
                   FROM quote_processing qp LEFT JOIN quote_audit qa ON qa.processing_id = qp.processing_id
                   WHERE qp.processing_id = %s""",
                (processing_id,),
            )
            header_row = cursor.fetchone()
            header = None
            if header_row is not None:
                assert cursor.description is not None, "a fetched row implies a resultset description exists"
                cols = [d[0] for d in cursor.description]
                header = dict(zip(cols, header_row))

            cursor.execute(
                """SELECT qli.line_item_id, qli.bom_id, qli.evco_part_id, qli.customer_part_number,
                          lia.aka_id, lia.pricing_action, lia.pricing_before_value, lia.pricing_after_value,
                          lia.inactivated_price_rows, lia.sales_order_id, lia.sales_order_note,
                          lia.rule_codes_applied, lia.line_status
                   FROM quote_line_items qli LEFT JOIN line_item_audit lia ON lia.line_item_id = qli.line_item_id
                   WHERE qli.processing_id = %s ORDER BY qli.line_number, qli.created_at""",
                (processing_id,),
            )
            assert cursor.description is not None, "a SELECT always produces a resultset description"
            cols = [d[0] for d in cursor.description]
            line_items = [dict(zip(cols, row)) for row in cursor.fetchall()]

        return {"header": header, "line_items": line_items}

    def get_exceptions_for_line_item(self, line_item_id: UUID) -> list[str]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT exception_code FROM exception_logs WHERE line_item_id = %s ORDER BY created_at",
                (line_item_id,),
            )
            return [row[0] for row in cursor.fetchall() if row[0]]
