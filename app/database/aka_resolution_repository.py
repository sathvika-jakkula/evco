"""
Persistence for aka_resolution_results - records a real per-lookup DECISION
(NOT_FOUND / CREATED / NO_CHANGE / UPDATED), derived by comparing before vs.
after values, not just the raw CRUD operation InventoryMockStore performed.
Called from app/modules/inventory/store.py after get_aka/create_aka/update_aka
already ran - this repository makes no AKA decisions of its own, it only
classifies and records what those methods already returned.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class AkaResolutionRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def record_resolution(
        self,
        line_item_id: Optional[UUID],
        aka_id: Optional[str],
        ar_invt_id: Optional[str],
        ar_cust_id: Optional[str],
        bom_id: Optional[str],
        decision: str,
        status: str,
        before_value: Optional[dict[str, Any]],
        after_value: Optional[dict[str, Any]],
    ) -> UUID:
        aka_result_id = uuid4()
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO aka_resolution_results
                   (aka_result_id, line_item_id, aka_id, ar_invt_id, ar_cust_id, bom_id,
                    decision, status, before_value, after_value, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
                (aka_result_id, line_item_id, aka_id, ar_invt_id, ar_cust_id, bom_id,
                 decision, status,
                 _to_json(before_value), _to_json(after_value),
                 datetime.now(timezone.utc)),
            )
        return aka_result_id

    def get_latest_for_line_item(self, line_item_id: UUID) -> Optional[dict[str, Any]]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT decision, status, aka_id, before_value, after_value
                   FROM aka_resolution_results WHERE line_item_id = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (line_item_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            assert cursor.description is not None, "a fetched row implies a resultset description exists"
            columns = [d[0] for d in cursor.description]
            return dict(zip(columns, row))


def _to_json(value: Optional[dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str)
