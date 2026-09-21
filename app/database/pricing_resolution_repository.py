"""
Persistence for pricing_results - records a real per-lookup DECISION
(CREATED / NO_CHANGE / UPDATED), derived by comparing the new price against
whatever was previously active in pricing_history, not just the raw
add/update_price_break call PriceBreakService performed. Called from
app/modules/pricing/service.py after that service's own methods already
decided the price - this repository makes no pricing decisions of its own.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class PricingResolutionRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def record_resolution(
        self,
        line_item_id: Optional[UUID],
        pricing_action: str,
        pricing_before_value: Optional[float],
        pricing_after_value: Optional[float],
        inactivated_price_rows: Optional[list[Any]],
        decision: str,
        status: str,
    ) -> UUID:
        pricing_result_id = uuid4()
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO pricing_results
                   (pricing_result_id, line_item_id, pricing_action, pricing_before_value,
                    pricing_after_value, inactivated_price_rows, decision, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)""",
                (pricing_result_id, line_item_id, pricing_action, pricing_before_value,
                 pricing_after_value,
                 json.dumps(inactivated_price_rows, default=str) if inactivated_price_rows is not None else None,
                 decision, status, datetime.now(timezone.utc)),
            )
        return pricing_result_id

    def get_latest_for_line_item(self, line_item_id: UUID) -> Optional[dict[str, Any]]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT decision, status, pricing_before_value, pricing_after_value, inactivated_price_rows
                   FROM pricing_results WHERE line_item_id = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (line_item_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            assert cursor.description is not None, "a fetched row implies a resultset description exists"
            columns = [d[0] for d in cursor.description]
            return dict(zip(columns, row))
