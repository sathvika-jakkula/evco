"""
PricingHistoryRepository: persistence for pricing_history - the one genuinely
new table added to support price versioning (PriceBreakService, the existing
in-memory mock, never had any DB-backed history at all).

Written in the exact same style as the existing repositories
(app/database/quote_processing_repository.py, etc.): raw SQL via psycopg,
DatabaseConnection, client-generated UUID primary keys. Called directly from
app/modules/pricing/service.py's PriceBreakService, after that service's own
add_price_break/update_price_break methods already decided the result - this
repository makes no pricing decisions of its own, it only stores them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class PricingHistoryRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def get_active_price(
        self, customer_number: str, evco_part_number: str, manufacturing_bom_number: str | None, quantity: int
    ) -> dict | None:
        """The currently active pricing_history row for this business key/tier, if any."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT pricing_history_id, price, effective_date, inactive_date, is_active,
                          source_quote_number, source_processing_id
                   FROM pricing_history
                   WHERE customer_number = %s AND evco_part_number = %s
                     AND manufacturing_bom_number IS NOT DISTINCT FROM %s
                     AND quantity = %s AND is_active = true
                   ORDER BY effective_date DESC LIMIT 1""",
                (customer_number, evco_part_number, manufacturing_bom_number, quantity),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            assert cursor.description is not None, "a fetched row implies a resultset description exists"
            columns = [d[0] for d in cursor.description]
            return dict(zip(columns, row))

    def get_active_price_for_quantity(
        self, customer_number: str, evco_part_number: str, quantity: float
    ) -> dict | None:
        """Used only for the sales-order price comparison (SalesOrderData carries no BOM
        or exact tier quantity - a real sales order's ordered quantity, not a quote's tier
        quantity). Picks the active tier with the largest quantity break at or below the
        order's quantity - the standard "which tier applies" quantity-break lookup, used
        here purely to state a factual price comparison, not to decide anything."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT pricing_history_id, price, quantity, manufacturing_bom_number
                   FROM pricing_history
                   WHERE customer_number = %s AND evco_part_number = %s
                     AND is_active = true AND quantity <= %s
                   ORDER BY quantity DESC LIMIT 1""",
                (customer_number, evco_part_number, quantity),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            assert cursor.description is not None, "a fetched row implies a resultset description exists"
            columns = [d[0] for d in cursor.description]
            return dict(zip(columns, row))

    def get_all_tiers(
        self, customer_number: str, evco_part_number: str, manufacturing_bom_number: str | None
    ) -> list[dict]:
        """All quantity tiers (active + inactive) for a business key, for the reporting
        module - not filtered to one quantity like get_history/get_active_price."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT pricing_history_id, quantity, price, is_active, inactive_date,
                          line_item_id AS created_by_line_item_id, inactivated_by_line_item_id
                   FROM pricing_history
                   WHERE customer_number = %s AND evco_part_number = %s
                     AND manufacturing_bom_number IS NOT DISTINCT FROM %s
                   ORDER BY quantity ASC, effective_date DESC""",
                (customer_number, evco_part_number, manufacturing_bom_number),
            )
            rows = cursor.fetchall()
            assert cursor.description is not None, "a SELECT always produces a resultset description"
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    def record_price_change(
        self,
        customer_number: str,
        evco_part_number: str,
        manufacturing_bom_number: str | None,
        quantity: int,
        new_price: float,
        currency: str,
        new_effective_date: datetime,
        source_quote_number: str | None,
        source_processing_id: UUID | None,
        line_item_id: UUID | None = None,
    ) -> UUID:
        """
        BR-015/BR-016 style persistence: expire the currently-active row for
        this business key one moment before the new effective date (never
        overwrite it), then insert the new version as active. If no active
        row exists yet, this simply inserts the first version.

        This method only RECORDS a price the caller (an already-completed
        add_price_break/update_price_break call, or a test) supplies - it does
        not decide whether a price should change. The actual BR-015/BR-016
        decision logic is not implemented anywhere in the existing
        PriceBreakService today (see evco_mock_data/data/rule_catalog.py,
        marked "agent-deferred"); this repository only provides the missing
        storage, per the plan's "additive, not a new business-rule engine" design.
        """
        now = datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """UPDATE pricing_history SET is_active = false, inactive_date = %s, updated_at = %s,
                          inactivated_by_line_item_id = %s
                   WHERE customer_number = %s AND evco_part_number = %s
                     AND manufacturing_bom_number IS NOT DISTINCT FROM %s
                     AND quantity = %s AND is_active = true""",
                (new_effective_date, now, line_item_id,
                 customer_number, evco_part_number, manufacturing_bom_number, quantity),
            )
            new_id = uuid4()
            cursor.execute(
                """INSERT INTO pricing_history
                   (pricing_history_id, evco_part_number, customer_number, manufacturing_bom_number,
                    quantity, price, currency, effective_date, inactive_date, is_active,
                    source_quote_number, source_processing_id, line_item_id, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, true, %s, %s, %s, %s, %s)""",
                (new_id, evco_part_number, customer_number, manufacturing_bom_number, quantity, new_price,
                 currency, new_effective_date, source_quote_number, source_processing_id, line_item_id, now, now),
            )
            return new_id

    def get_history(
        self, customer_number: str, evco_part_number: str, manufacturing_bom_number: str | None, quantity: int
    ) -> list[dict]:
        """Full version history (active + inactive) for a business key/tier, oldest first -
        lets a caller determine previous price, new price, effective/inactive dates, source
        quote, and active/inactive status, per the plan's traceability requirement."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT pricing_history_id, price, currency, effective_date, inactive_date, is_active,
                          source_quote_number, source_processing_id, created_at, updated_at
                   FROM pricing_history
                   WHERE customer_number = %s AND evco_part_number = %s
                     AND manufacturing_bom_number IS NOT DISTINCT FROM %s AND quantity = %s
                   ORDER BY effective_date ASC""",
                (customer_number, evco_part_number, manufacturing_bom_number, quantity),
            )
            rows = cursor.fetchall()
            assert cursor.description is not None, "a SELECT always produces a resultset description"
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
