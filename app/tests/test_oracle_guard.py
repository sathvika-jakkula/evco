import sys; sys.path.insert(0, ".")
from unittest.mock import MagicMock, patch
import pytest
from app.integrations import oracle_iqms as o

def test_both_queries_are_select_only():
    assert o.assert_select_only(o.AKA_SQL).startswith("SELECT")
    assert o.assert_select_only(o.PRICE_BREAKS_SQL).startswith("SELECT")

@pytest.mark.parametrize("bad", [
    "DELETE FROM iqms.aka",
    "UPDATE iqms.aka SET min_sell_qty = 1",
    "DROP TABLE iqms.aka",
    "SELECT 1 FROM dual; DROP TABLE iqms.aka",
    "SELECT * FROM iqms.aka WHERE 1=1 FOR UPDATE; DELETE FROM x",
    "INSERT INTO x VALUES (1)",
])
def test_writes_are_refused(bad):
    with pytest.raises(o.ReadOnlyViolation):
        o.assert_select_only(bad)

def test_queries_use_bind_variables_not_values():
    for sql in (o.AKA_SQL, o.PRICE_BREAKS_SQL):
        assert ":item_no" in sql and ":cust_no" in sql and ":mfg_no" in sql
        assert "9462500" not in sql and "10192" not in sql

def test_run_select_sets_read_only_and_binds_values():
    cursor = MagicMock()
    cursor.description = [("ITEM_NO",), ("CUST_NO",)]
    cursor.fetchall.return_value = [("9462500", "10192")]
    conn = MagicMock(); conn.cursor.return_value = cursor
    with patch.object(o, "_connect", return_value=conn):
        rows = o.get_aka_rows("9462500", "10192", "5720/9462500")
    executed = [c.args for c in cursor.execute.call_args_list]
    assert executed[0] == ("SET TRANSACTION READ ONLY",)
    assert executed[1][1] == {"item_no": "9462500", "cust_no": "10192", "mfg_no": "5720/9462500"}
    assert rows == [{"item_no": "9462500", "cust_no": "10192"}]
    conn.close.assert_called_once()
