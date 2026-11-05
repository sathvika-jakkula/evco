"""
Read-only access to the IQMS Oracle database for AKA and price-break lookups.

Only SELECT statements are allowed. Every statement is checked before it runs, the
transaction is set to READ ONLY, and the values are passed as bind variables.
Credentials come from the environment (ORACLE_DSN, ORACLE_USER, ORACLE_PASSWORD).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from app.core.config import settings

logger = logging.getLogger(__name__)

AKA_SQL = """
SELECT i.itemno        AS item_no,
       i.rev           AS item_rev,
       i.descrip       AS item_descrip,
       a.id            AS aka_id,
       a.cust_itemno   AS aka_item_no,
       a.cust_descrip  AS aka_descrip,
       a.info_so       AS so_item_info,
       a.cust_rev      AS aka_rev,
       c.custno        AS cust_no,
       c.company       AS cust_name,
       cur.descrip     AS currency,
       s.mfgno         AS mfg_no,
       a.min_sell_qty,
       a.multiple
FROM   iqms.aka a
JOIN   iqms.arinvt i
       ON i.id = a.arinvt_id
JOIN   iqms.arcusto c
       ON c.id = a.arcusto_id
LEFT JOIN iqms.standard s
       ON s.id = a.standard_id
LEFT JOIN iqms.currency cur
       ON cur.id = NVL(a.currency_id, c.currency_id)
WHERE  i.itemno = :item_no
AND    TRIM(c.custno) = :cust_no
AND    s.mfgno = :mfg_no
"""

PRICE_BREAKS_SQL = """
SELECT ab.qprice                                 AS price,
       ab.quan                                   AS qty,
       ab.comment1                               AS comments,
       TO_CHAR(ab.effect_date,   'fmMM/DD/YYYY') AS effective_date,
       TO_CHAR(ab.deactive_date, 'fmMM/DD/YYYY') AS inactive_date,
       TO_CHAR(ab.price_date,    'fmMM/DD/YYYY') AS price_date
FROM   iqms.aka a
JOIN   iqms.arinvt i
       ON i.id = a.arinvt_id
JOIN   iqms.arcusto c
       ON c.id = a.arcusto_id
JOIN   iqms.standard s
       ON s.id = a.standard_id
JOIN   iqms.aka_breaks ab
       ON ab.aka_id = a.id
WHERE  i.itemno = :item_no
AND    TRIM(c.custno) = :cust_no
AND    s.mfgno = :mfg_no
ORDER  BY ab.effect_date, ab.quan
"""

# Words that must never appear in a statement run by this module.
_WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|COMMIT|ROLLBACK)\b",
    re.IGNORECASE,
)


class ReadOnlyViolation(RuntimeError):
    """Raised when a statement is not a plain SELECT."""


def assert_select_only(sql: str) -> str:
    """Return the statement without a trailing semicolon, or raise if it is not a single SELECT."""
    statement = sql.strip().rstrip(";").strip()
    if not re.match(r"^SELECT\b", statement, re.IGNORECASE):
        raise ReadOnlyViolation("Only SELECT statements may run against the IQMS Oracle database.")
    if ";" in statement:
        raise ReadOnlyViolation("Only one statement may run at a time.")
    if _WRITE_WORDS.search(statement):
        raise ReadOnlyViolation("Statement contains a write keyword and was not run.")
    return statement


def _connect():
    import oracledb

    if not settings.ORACLE_DSN or not settings.ORACLE_USER:
        raise RuntimeError("ORACLE_DSN and ORACLE_USER must be set in the environment.")
    return oracledb.connect(user=settings.ORACLE_USER, password=settings.ORACLE_PASSWORD, dsn=settings.ORACLE_DSN)


def run_select(sql: str, binds: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run one read-only SELECT with bind variables and return rows as dictionaries."""
    statement = assert_select_only(sql)
    connection = _connect()
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(statement, binds)
        columns = [d[0].lower() for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()


def get_aka_rows(item_no: str, cust_no: str, mfg_no: str) -> List[Dict[str, Any]]:
    return run_select(AKA_SQL, {"item_no": item_no, "cust_no": cust_no, "mfg_no": mfg_no})


def get_price_break_rows(item_no: str, cust_no: str, mfg_no: str) -> List[Dict[str, Any]]:
    return run_select(PRICE_BREAKS_SQL, {"item_no": item_no, "cust_no": cust_no, "mfg_no": mfg_no})
