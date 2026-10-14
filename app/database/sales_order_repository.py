"""
Persistence for sales orders/releases fetched through SalesOrderService
(app/modules/sales_order/service.py) - which itself has ZERO local storage
(always a live IQMS pass-through, per the earlier DB investigation). Same
raw-SQL/psycopg style as the existing repositories. Called directly from
SalesOrderService's own methods, after that service already fetched/parsed
the data - this repository makes no sales-order decisions, it only records
what was returned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.database.connection import DatabaseConnection


class SalesOrderSyncRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def upsert_sales_order(self, order, source: str = "dummy", line_item_id: UUID | None = None) -> None:
        """order: a SalesOrderData instance (real, unmodified schema)."""
        now = datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO sales_orders_synced (sales_order_id, sales_order_detail_id, ar_invt_id,
                       order_number, po_number, customer_number, company, item_number, description,
                       customer_item_number, status, total_qty_ordered, unit_price, date_taken, delivery_date,
                       source, line_item_id, synced_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (sales_order_id) DO UPDATE SET
                       sales_order_detail_id = EXCLUDED.sales_order_detail_id, ar_invt_id = EXCLUDED.ar_invt_id,
                       order_number = EXCLUDED.order_number, po_number = EXCLUDED.po_number,
                       customer_number = EXCLUDED.customer_number, company = EXCLUDED.company,
                       item_number = EXCLUDED.item_number, description = EXCLUDED.description,
                       customer_item_number = EXCLUDED.customer_item_number, status = EXCLUDED.status,
                       total_qty_ordered = EXCLUDED.total_qty_ordered, unit_price = EXCLUDED.unit_price,
                       date_taken = EXCLUDED.date_taken, delivery_date = EXCLUDED.delivery_date,
                       source = EXCLUDED.source,
                       line_item_id = COALESCE(EXCLUDED.line_item_id, sales_orders_synced.line_item_id),
                       synced_at = EXCLUDED.synced_at""",
                (order.sales_order_id, order.sales_order_detail_id, order.ar_invt_id, order.order_number,
                 order.po_number, order.customer_number, order.company, order.item_number, order.description,
                 order.customer_item_number, order.status, order.total_qty_ordered, order.unit_price,
                 order.date_taken, order.delivery_date, source, line_item_id, now),
            )

    def upsert_release(self, release, line_item_id: UUID | None = None) -> None:
        """release: a SalesOrderReleaseData instance (real, unmodified schema)."""
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO sales_order_releases_synced (release_id, sales_order_detail_id, qty,
                       must_ship_date, ship_date, line_item_id, synced_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (release_id) DO UPDATE SET
                       qty = EXCLUDED.qty, must_ship_date = EXCLUDED.must_ship_date,
                       ship_date = EXCLUDED.ship_date,
                       line_item_id = COALESCE(EXCLUDED.line_item_id, sales_order_releases_synced.line_item_id),
                       synced_at = EXCLUDED.synced_at""",
                (release.release_id, release.sales_order_detail_id, release.qty,
                 release.must_ship_date, release.ship_date, line_item_id, datetime.now(timezone.utc)),
            )

    def get_orders_for_item(self, item_number: str) -> list[dict]:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT sales_order_id, customer_number, company, item_number, total_qty_ordered,
                          unit_price, date_taken, source, line_item_id
                   FROM sales_orders_synced WHERE item_number = %s ORDER BY date_taken""",
                (item_number,),
            )
            rows = cursor.fetchall()
            assert cursor.description is not None, "a SELECT always produces a resultset description"
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    def update_sales_order_detail(
        self,
        sales_order_detail_id: int,
        unit_price: float | None = None,
        on_hold: bool | None = None,
        ship_hold: bool | None = None,
    ) -> dict | None:
        """Update sales order detail with optional price, on_hold, and ship_hold flags.
        Returns the updated record or None if not found."""
        with self.connection.connect() as db, db.cursor() as cursor:
            # First, fetch the current record to validate it exists and get current values
            cursor.execute(
                """SELECT sales_order_detail_id, unit_price, on_hold, ship_hold
                   FROM sales_orders_synced WHERE sales_order_detail_id = %s""",
                (sales_order_detail_id,),
            )
            current_row = cursor.fetchone()
            if current_row is None:
                return None

            # Build dynamic UPDATE with only provided fields
            updates = []
            values = []

            if unit_price is not None:
                updates.append("unit_price = %s")
                values.append(unit_price)
            if on_hold is not None:
                updates.append("on_hold = %s")
                values.append(on_hold)
            if ship_hold is not None:
                updates.append("ship_hold = %s")
                values.append(ship_hold)

            if not updates:
                # No updates requested, return current state
                return {
                    "sales_order_detail_id": current_row[0],
                    "unit_price": current_row[1],
                    "on_hold": current_row[2],
                    "ship_hold": current_row[3],
                    "updated_at": datetime.now(timezone.utc),
                }

            values.append(sales_order_detail_id)

            if len(updates) == 1:
                sql = """UPDATE sales_orders_synced
                       SET """ + updates[0] + """
                       WHERE sales_order_detail_id = %s
                       RETURNING sales_order_detail_id, unit_price, on_hold, ship_hold"""
            elif len(updates) == 2:
                sql = """UPDATE sales_orders_synced
                       SET """ + updates[0] + """, """ + updates[1] + """
                       WHERE sales_order_detail_id = %s
                       RETURNING sales_order_detail_id, unit_price, on_hold, ship_hold"""
            else:
                sql = """UPDATE sales_orders_synced
                       SET """ + updates[0] + """, """ + updates[1] + """, """ + updates[2] + """
                       WHERE sales_order_detail_id = %s
                       RETURNING sales_order_detail_id, unit_price, on_hold, ship_hold"""

            cursor.execute(sql, values)

            result = cursor.fetchone()
            if result is None:
                return None

            return {
                "sales_order_detail_id": result[0],
                "unit_price": result[1],
                "on_hold": result[2],
                "ship_hold": result[3],
                "updated_at": datetime.now(timezone.utc),
            }
