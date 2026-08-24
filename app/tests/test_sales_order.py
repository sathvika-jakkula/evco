from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import validate_access_token
import app.modules.sales_order.router as sales_order_router
from app.modules.sales_order.service import SalesOrderService

client = TestClient(app)

# Trimmed real IQMS SalesOrder rows (from the SalesOrder?filters=ArInvtId.eq~207192
# probe) - proves the server-side filter is broken (rows for other item
# numbers come back) and that SalesOrderService filters correctly client-side.
RAW_SALES_ORDERS = [
    {
        "ArCustoId": 26147, "Status": "Active", "ItemClass": "FG", "Rev": "11",
        "ItemNumber": "9410145", "Description": "47219 BUMPER HILLROM",
        "CustomerItemNumber": "47219", "CustomerDescription": "BUMPER",
        "TotalQTYOrdered": 5025, "ArInvtId": 207192, "UnitPrice": 2.03,
        "DeliveryDate": "0001-01-01T00:00:00", "Id": 15355, "PONumber": "000000008238",
        "OrderNumber": "502598-CAL", "CustomerNumber": "11903", "Company": "PINNACLE PLASTIC PRODUCTS",
        "DateTaken": "2026-05-26T00:00:00", "OrdDetailId": 59194,
    },
    {
        "ArCustoId": 26096, "Status": "Active", "ItemClass": "FG", "Rev": "B",
        "ItemNumber": "9462067", "Description": "40404132 - LH SASH PANEL TAYLOR MADE",
        "CustomerItemNumber": "40404132", "CustomerDescription": "LH SASH PANEL TAYLOR MADE",
        "TotalQTYOrdered": 1040, "ArInvtId": 208885, "UnitPrice": 9.4,
        "DeliveryDate": "0001-01-01T00:00:00", "Id": 13187, "PONumber": "0002289643",
        "OrderNumber": "403693-OSH", "CustomerNumber": "11844", "Company": "LIPPERT COMPONENTS",
        "DateTaken": "2026-03-02T00:00:00", "OrdDetailId": 44146,
    },
]

RAW_SALES_ORDER_DETAILS = [
    {
        "Id": 32969, "SalesOrderId": 2099, "ArInvtId": 210507, "BlanketQty": 9776.0,
        "UnitPrice": 11.54, "ListUnitPrice": 11.54, "OnHold": False, "ShipHold": False,
        "Discount": 0.0, "UOM": "EACH", "Containers": 752.0, "DropShip": False,
        "POInfo": "FORECAST-SUBZERO", "CUser1": "REV MISMATCH. ITEM REV: E AKA ITEM REV: 9040898E",
    },
    {
        "Id": 15691, "SalesOrderId": 2099, "ArInvtId": 209042, "BlanketQty": 600.0,
        "UnitPrice": 1.67, "ListUnitPrice": 1.67, "OnHold": False, "ShipHold": True,
        "Discount": 0.0, "UOM": "EACH", "Containers": 10.0, "DropShip": False,
        "POInfo": "FORECAST-SUBZERO", "CUser1": "REV MISMATCH. ITEM REV: E AKA ITEM REV: 7022907E",
    },
]

RAW_SALES_ORDER_RELEASES = [
    {
        "Id": 3610040, "SalesOrderDetailId": 3476, "Seq": 1, "Qty": 240.0,
        "RequestDate": "2026-09-14T00:00:00", "PromiseDate": "2026-09-14T00:00:00",
        "Forecast": "Y", "OriginalQty": 240.0, "DateType": "DL",
        "MustShipDate": "2026-09-03T00:00:00", "Acknowledged": False,
        "ArInvtId": 209092, "ShipDate": "0001-01-01T00:00:00", "ShippedQty": 0.0,
        "LeftToShip": 240.0,
    },
]


@pytest.fixture(autouse=True)
def override_auth_dependency():
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "test-user-id"}
    yield
    app.dependency_overrides.clear()


def test_get_sales_orders_filters_client_side_since_iqms_filter_is_broken():
    """SalesOrderService.get_sales_orders must filter, since IQMS's own filters= param doesn't."""
    fake_client = MagicMock()
    fake_client.get_sales_orders.return_value = RAW_SALES_ORDERS
    service = SalesOrderService(iqms_client=fake_client)

    results = service.get_sales_orders(item_number="9410145")

    assert len(results) == 1
    assert results[0].item_number == "9410145"
    assert results[0].ar_invt_id == 207192
    assert results[0].sales_order_id == 15355
    assert results[0].sales_order_detail_id == 59194
    assert results[0].order_number == "502598-CAL"
    assert results[0].delivery_date is None  # "0001-01-01" sentinel normalized to None
    assert results[0].date_taken is not None


def test_get_sales_orders_endpoint(monkeypatch):
    fake_service = MagicMock()
    fake_service.get_sales_orders.return_value = SalesOrderService(
        iqms_client=MagicMock(get_sales_orders=MagicMock(return_value=RAW_SALES_ORDERS))
    ).get_sales_orders(item_number="9410145")
    monkeypatch.setattr(sales_order_router, "sales_order_service", fake_service)

    response = client.post("/sales-orders/get-sales-orders", json={"item_number": "9410145"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["item_number"] == "9410145"
    fake_service.get_sales_orders.assert_called_once_with(item_number="9410145")


def test_get_sales_order_details_filters_client_side_by_ar_invt_id():
    """One sales order can have multiple line items; must filter to the requested ar_invt_id."""
    fake_client = MagicMock()
    fake_client.get_sales_order_details.return_value = RAW_SALES_ORDER_DETAILS
    service = SalesOrderService(iqms_client=fake_client)

    results = service.get_sales_order_details(sales_order_id=2099, ar_invt_id=209042)

    assert len(results) == 1
    assert results[0].ar_invt_id == 209042
    assert results[0].sales_order_detail_id == 15691
    fake_client.get_sales_order_details.assert_called_once_with(2099)


def test_get_sales_order_details_endpoint(monkeypatch):
    fake_service = MagicMock()
    fake_service.get_sales_order_details.return_value = SalesOrderService(
        iqms_client=MagicMock(get_sales_order_details=MagicMock(return_value=RAW_SALES_ORDER_DETAILS))
    ).get_sales_order_details(sales_order_id=2099, ar_invt_id=210507)
    monkeypatch.setattr(sales_order_router, "sales_order_service", fake_service)

    response = client.post(
        "/sales-orders/get-sales-order-details",
        json={"sales_order_id": 2099, "ar_invt_id": 210507},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"][0]["sales_order_id"] == 2099
    assert data["data"][0]["ar_invt_id"] == 210507
    assert data["data"][0]["note"] == "REV MISMATCH. ITEM REV: E AKA ITEM REV: 9040898E"
    fake_service.get_sales_order_details.assert_called_once_with(sales_order_id=2099, ar_invt_id=210507)


def test_get_sales_order_releases_endpoint(monkeypatch):
    fake_service = MagicMock()
    fake_service.get_sales_order_releases.return_value = SalesOrderService(
        iqms_client=MagicMock(get_sales_order_releases=MagicMock(return_value=RAW_SALES_ORDER_RELEASES))
    ).get_sales_order_releases(sales_order_detail_id=3476)
    monkeypatch.setattr(sales_order_router, "sales_order_service", fake_service)

    response = client.post("/sales-orders/get-sales-order-releases", json={"sales_order_detail_id": 3476})
    assert response.status_code == 200
    data = response.json()
    assert data["data"][0]["sales_order_detail_id"] == 3476
    assert data["data"][0]["left_to_ship"] == 240.0
    assert data["data"][0]["ship_date"] is None  # sentinel normalized to None
    fake_service.get_sales_order_releases.assert_called_once_with(sales_order_detail_id=3476)


def test_sales_order_endpoints_require_auth():
    app.dependency_overrides.clear()
    response = client.post("/sales-orders/get-sales-orders", json={"item_number": "9410145"})
    assert response.status_code == 401
