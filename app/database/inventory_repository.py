"""
Persistence for AKA records - the DB storage InventoryMockStore
(app/modules/inventory/store.py) never had (it's a pure in-memory dict,
lost on restart). Same raw-SQL/psycopg style as the existing repositories.

Called directly from InventoryMockStore's own methods (get_aka, create_aka,
update_aka) in app/modules/inventory/store.py, AFTER that method has already
decided the in-memory result - this repository makes no inventory decisions
of its own, it only stores what was already decided. Every write is
best-effort (exceptions are caught and logged in store.py, not raised here),
so a DB outage never breaks the mock store's own behavior.

Note: search-part and get-bom-candidates were removed from the Inventory
module (no longer needed), so this repository no longer has upsert_part/
upsert_bom_candidate methods. The inventory_items/bom_candidates tables from
migration 0001 are unused but were left in place rather than dropped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class InventoryRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def upsert_aka_record(
        self, customer_number: str, customer_part_number: str, item_number: str, aka_description: str,
        item_description: str, uom: str, currency: str, manufacturing_bom_number: str | None,
        moq: int, selling_multiples_of: int, rev: str = "", customer_name: str = "",
        ship_to_attn: str = "", status: str = "CREATED", line_item_id: UUID | None = None,
    ) -> UUID:
        """Backs POST /inventory/get-aka, /create-aka, /update-aka
        (InventoryMockStore.get_aka/create_aka/update_aka).

        Keyed on (customer_number, item_number, manufacturing_bom_number) -
        matching InventoryMockStore's own AkaDetailKey - NOT
        customer_part_number, since akaItem# (customer_part_number) is a
        mutable attribute updatable via update_aka, not part of the record's
        identity. Calling this again for the same triple updates the existing
        row's fields in place rather than creating a duplicate.

        `status` records the most recent operation performed on this row
        (CREATED / UPDATED / FETCHED) - it is NOT a business-rule outcome,
        just an audit-style marker of what InventoryMockStore last did.
        """
        now = datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO aka_records (aka_record_id, customer_number, customer_part_number, item_number,
                       aka_description, item_description, uom, currency, manufacturing_bom_number, moq,
                       selling_multiples_of, rev, customer_name, ship_to_attn, status, line_item_id,
                       created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (customer_number, item_number, manufacturing_bom_number) DO UPDATE SET
                       customer_part_number = EXCLUDED.customer_part_number,
                       aka_description = EXCLUDED.aka_description, item_description = EXCLUDED.item_description,
                       uom = EXCLUDED.uom, currency = EXCLUDED.currency, moq = EXCLUDED.moq,
                       selling_multiples_of = EXCLUDED.selling_multiples_of,
                       rev = EXCLUDED.rev, customer_name = EXCLUDED.customer_name,
                       ship_to_attn = EXCLUDED.ship_to_attn, status = EXCLUDED.status,
                       line_item_id = COALESCE(EXCLUDED.line_item_id, aka_records.line_item_id),
                       updated_at = EXCLUDED.updated_at
                   RETURNING aka_record_id""",
                (uuid4(), customer_number, customer_part_number, item_number, aka_description, item_description,
                 uom, currency, manufacturing_bom_number, moq, selling_multiples_of, rev, customer_name,
                 ship_to_attn, status, line_item_id, now, now),
            )
            row = cursor.fetchone()
            assert row is not None, "INSERT ... RETURNING always yields exactly one row"
            return row[0]

    def get_aka_record(self, customer_number: str, item_number: str, manufacturing_bom_number: str) -> dict | None:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT aka_record_id, customer_number, customer_part_number, item_number, aka_description,
                          item_description, uom, currency, manufacturing_bom_number, moq, selling_multiples_of,
                          rev, customer_name, ship_to_attn, status, created_at, updated_at
                   FROM aka_records WHERE customer_number = %s AND item_number = %s AND manufacturing_bom_number = %s""",
                (customer_number, item_number, manufacturing_bom_number),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            assert cursor.description is not None, "a fetched row implies a resultset description exists"
            columns = [d[0] for d in cursor.description]
            return dict(zip(columns, row))
