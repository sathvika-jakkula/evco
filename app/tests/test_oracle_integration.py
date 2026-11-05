from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import validate_access_token
from app.integrations import oracle_iqms
from app.main import app
from app.modules.inventory.service import InventoryService

client = TestClient(app)

AKA_ROW = {
    "item_no": "9462500", "item_rev": "E", "item_descrip": "7022908 TRIM,DOOR TOP LH 30in  SUB-ZERO",
    "aka_id": 12996, "aka_item_no": "7022908", "aka_descrip": "TRIM,DOOR TOP LH 30in  SUB-ZERO",
    "so_item_info": "MOLD 5720", "aka_rev": "E", "cust_no": "10192", "cust_name": "SUB-ZERO INC",
    "currency": "US Dollar", "mfg_no": "5720/9462500", "min_sell_qty": None, "multiple": None,
}
PRICE_ROW = {
    "price": 4.6, "qty": 1.0, "comments": "63833-004", "effective_date": "8/31/2026",
    "inactive_date": None, "price_date": "8/27/2026",
}


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "t", "tenant": "t"}
    monkeypatch.setattr(settings, "IQMS_DATA_SOURCE", "live")
    yield
    app.dependency_overrides.clear()


def _service():
    return InventoryService(
        iqms_client=MagicMock(), customer_repository=MagicMock(),
        resolution_repository=MagicMock(), processing_repository=MagicMock(),
    )


def test_get_aka_maps_oracle_rows(monkeypatch):
    monkeypatch.setattr(oracle_iqms, "get_aka_rows", lambda *a: [AKA_ROW])
    result = _service().get_aka_search("9462500", "10192", "5720/9462500")
    assert result.header.item_number == "9462500"
    assert result.header.rev == "E"
    detail = result.aka_details[0]
    assert detail.aka_item_number == "7022908"
    assert detail.customer_name == "SUB-ZERO INC"
    assert detail.currency == "US Dollar"
    assert detail.manufacturing_bom_number == "5720/9462500"
    assert detail.so_item_number == "MOLD 5720"


def test_get_aka_endpoint_returns_only_present_fields(monkeypatch):
    monkeypatch.setattr(oracle_iqms, "get_aka_rows", lambda *a: [AKA_ROW])
    response = client.post("/inventory/get-aka", json={
        "evco_part_number": "9462500", "customer_number": "10192", "manufacturing_bom_number": "5720/9462500",
    })
    assert response.status_code == 200
    detail = response.json()["data"]["aka_details"][0]
    assert detail["currency"] == "US Dollar"
    assert "minimum_selling_qty" not in detail  # Oracle has no value, so it is left out


def test_get_aka_no_rows_is_200_with_null_header(monkeypatch):
    monkeypatch.setattr(oracle_iqms, "get_aka_rows", lambda *a: [])
    response = client.post("/inventory/get-aka", json={
        "evco_part_number": "NOPE", "customer_number": "10192", "manufacturing_bom_number": "x",
    })
    assert response.status_code == 200
    assert response.json()["data"] == {"header": None, "aka_details": []}


def test_get_pricebreaks_maps_oracle_rows(monkeypatch):
    monkeypatch.setattr(oracle_iqms, "get_price_break_rows", lambda *a: [PRICE_ROW])
    breaks = _service().get_price_breaks("10192", "9462500", "5720/9462500")
    assert (breaks[0].unit_price, breaks[0].quantity, breaks[0].comment) == (4.6, 1, "63833-004")
    assert breaks[0].effective_date == "8/31/2026"
    assert breaks[0].inactive_date is None


def test_get_pricebreaks_endpoint_no_rows_is_empty_list(monkeypatch):
    monkeypatch.setattr(oracle_iqms, "get_price_break_rows", lambda *a: [])
    response = client.post("/inventory/get-pricebreaks", json={
        "evco_part_number": "9462500", "customer_number": "10192", "manufacturing_bom_number": "5720/9462500",
    })
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_bom_is_required_for_live_lookups():
    response = client.post("/inventory/get-aka", json={"evco_part_number": "9462500", "customer_number": "10192"})
    assert response.status_code == 400
