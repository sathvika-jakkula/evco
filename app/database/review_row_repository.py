"""Persistence for review rows: the rows each review agent returns for one quote line."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection
from app.core.exceptions import BusinessException


class ReviewRowRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def replace_rows(
        self,
        processing_id: UUID,
        line_item_id: UUID,
        review_area: str,
        quote_number: str,
        line_context: Dict[str, Optional[str]],
        rows: Sequence[Dict[str, Any]],
    ) -> tuple[List[UUID], int]:
        """Replace every stored row for (line_item_id, review_area) with rows, in one transaction.
        Returns (new review_row_ids, number of previously stored rows that were replaced)."""
        now = datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT processing_id, line_number FROM quote_line_items WHERE line_item_id = %s",
                (line_item_id,),
            )
            line = cursor.fetchone()
            if line is None or line[0] != processing_id:
                raise BusinessException(
                    message="line_item_id does not belong to the given processing_id",
                    code="LINE_ITEM_NOT_FOUND",
                    status_code=400,
                    details={"processing_id": str(processing_id), "line_item_id": str(line_item_id)},
                )
            line_number = line_context.get("line_number")
            line_number_value = int(line_number) if line_number not in (None, "") else line[1]

            cursor.execute(
                "DELETE FROM review_rows WHERE line_item_id = %s AND review_area = %s",
                (line_item_id, review_area),
            )
            replaced = cursor.rowcount

            ids: List[UUID] = []
            for index, row in enumerate(rows):
                review_row_id = uuid4()
                ids.append(review_row_id)
                cursor.execute(
                    """INSERT INTO review_rows (
                       review_row_id, processing_id, line_item_id, review_area, quote_number,
                       line_number, evco_part_number, mold_number, manufacturing_bom_number, customer_number,
                       sort_order, rule_code, check_name, status, message, field, record_ref,
                       sales_order_id, sales_order_detail_id, release_id,
                       quote_value, system_value, proposed_value, exception_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        review_row_id, processing_id, line_item_id, review_area, quote_number,
                        line_number_value, line_context.get("evco_part_number"), line_context.get("mold_number"),
                        line_context.get("manufacturing_bom_number"), line_context.get("customer_number"),
                        index, row["rule_code"], row["check"], row["status"], row["message"],
                        row.get("field"), row.get("record_ref"), row.get("sales_order_id"),
                        row.get("sales_order_detail_id"), row.get("release_id"),
                        row.get("quote_value"), row.get("system_value"), row.get("proposed_value"),
                        row.get("exception_id"), now,
                    ),
                )
        return ids, replaced

    def get_rows_for_processing(self, processing_id: UUID) -> List[Dict[str, Any]]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT review_row_id, line_item_id, line_number, review_area, evco_part_number,
                   mold_number, manufacturing_bom_number, customer_number, sort_order, rule_code,
                   check_name, status, message, field, record_ref, sales_order_id, sales_order_detail_id,
                   release_id, quote_value, system_value, proposed_value, exception_id, created_at
                   FROM review_rows WHERE processing_id = %s""",
                (processing_id,),
            )
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
