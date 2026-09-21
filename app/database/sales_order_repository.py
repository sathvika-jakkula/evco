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
