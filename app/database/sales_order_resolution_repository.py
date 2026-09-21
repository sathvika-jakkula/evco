"""
Persistence for sales_order_results - records an observed fact per line item
(NOT_FOUND / NO_CHANGE / MISMATCH), comparing a sales order's stored unit
price against the currently active pricing_history price. SalesOrderService
is a pure read-only IQMS pass-through - it never places a hold or changes an
order - so "decision" here is a factual classification, not a mutation
outcome, and "sales_order_hold"/"sales_order_note" describe what was
observed, never an action this app took.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class SalesOrderResolutionRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def record_resolution(
        self,
        line_item_id: Optional[UUID],
        sales_order_id: Optional[str],
        sales_order_note: Optional[str],
        updates: Optional[list[Any]],
        decision: str,
        status: str,
    ) -> UUID:
        sales_order_result_id = uuid4()
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO sales_order_results
                   (sales_order_result_id, line_item_id, sales_order_hold, sales_order_id,
                    sales_order_note, updates, decision, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)""",
                (sales_order_result_id, line_item_id, False, sales_order_id,
                 sales_order_note,
                 json.dumps(updates, default=str) if updates is not None else None,
                 decision, status, datetime.now(timezone.utc)),
            )
        return sales_order_result_id

    def get_all_for_line_item(self, line_item_id: UUID) -> list[dict[str, Any]]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT sales_order_id, decision, status, sales_order_note
                   FROM sales_order_results WHERE line_item_id = %s ORDER BY created_at""",
                (line_item_id,),
            )
            rows = cursor.fetchall()
            assert cursor.description is not None, "a SELECT always produces a resultset description"
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
