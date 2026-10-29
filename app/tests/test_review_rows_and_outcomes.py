from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.exceptions import BusinessException
from app.core.security import validate_access_token
from app.main import app
from app.modules.reporting.service import ReportingService
from app.modules.sales_order.service import SalesOrderService

client = TestClient(app)


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "test-user-id", "tenant": "test-tenant"}
    monkeypatch.setattr(settings, "IQMS_DATA_SOURCE", "live")
    yield
    app.dependency_overrides.clear()


def _review_payload(**overrides):
    payload = {
        "processing_id": str(uuid4()),
        "line_item_id": str(uuid4()),
        "review_area": "AKA",
        "quote_number": "74021-001",
        "line_number": "2",
        "evco_part_number": "9480026",
        "customer_number": "10329",
        "manufacturing_bom_number": "6601/9480026-CHIMEI",
        "rows": [{
            "rule_code": "VR-009", "check": "AKA manufacturing number", "status": "UPDATE_REQUIRED",
            "message": "AKA mfg # differs from quote", "field": "mfg#",
            "quote_value": "MFG-88213/A", "system_value": None, "proposed_value": "MFG-88213/A",
        }],
    }
    payload.update(overrides)
    return payload


def _route_with_repo(monkeypatch, ids, replaced):
    from app.modules.quote_processing import router as qp_router
    repo = MagicMock()
    repo.replace_rows.return_value = (ids, replaced)
    monkeypatch.setattr(qp_router.quote_processing_result_service, "review_row_repository", repo)
    return repo


def test_record_review_rows_returns_201_with_counts(monkeypatch):
    new_id = uuid4()
    repo = _route_with_repo(monkeypatch, [new_id], replaced=3)

    response = client.post("/quote-processing/record-review-rows", json=_review_payload())

    assert response.status_code == 201
    assert response.json()["data"] == {"rows_stored": 1, "rows_replaced": 3, "review_row_ids": [str(new_id)]}
    assert repo.replace_rows.call_args.kwargs["review_area"] == "AKA"
    assert repo.replace_rows.call_args.kwargs["line_context"]["manufacturing_bom_number"] == "6601/9480026-CHIMEI"


def test_empty_rows_is_valid_and_clears_the_area(monkeypatch):
    repo = _route_with_repo(monkeypatch, [], replaced=2)

    response = client.post("/quote-processing/record-review-rows", json=_review_payload(rows=[]))

    assert response.status_code == 201
    assert response.json()["data"]["rows_stored"] == 0
    assert response.json()["data"]["rows_replaced"] == 2
    assert repo.replace_rows.call_args.kwargs["rows"] == []


def test_record_review_rows_rejects_unknown_status_and_too_many_rows(monkeypatch):
    _route_with_repo(monkeypatch, [], replaced=0)
    bad_status = _review_payload()
    bad_status["rows"][0]["status"] = "MAYBE"
    assert client.post("/quote-processing/record-review-rows", json=bad_status).status_code == 400

    too_many = _review_payload(rows=[bad_status["rows"][0] | {"status": "NO_ACTION"}] * 201)
    assert client.post("/quote-processing/record-review-rows", json=too_many).status_code == 400


def test_get_aka_returns_200_with_null_header_for_unknown_customer(monkeypatch):
    from app.modules.inventory import router as inventory_router

    def not_found(**kwargs):
        raise BusinessException(message="x", code="CUSTOMER_NOT_FOUND", status_code=404)

    monkeypatch.setattr(inventory_router.inventory_service, "get_aka_search", not_found)

    response = client.post("/inventory/get-aka", json={"evco_part_number": "9480026", "customer_number": "99999"})

    assert response.status_code == 200
    assert response.json()["data"] == {"header": None, "aka_details": []}


def test_get_pricebreaks_returns_200_with_empty_list(monkeypatch):
    from app.modules.pricing import router as pricing_router

    monkeypatch.setattr(pricing_router.inventory_service, "get_price_breaks", lambda **kwargs: [])

    response = client.post("/inventory/get-pricebreaks", json={"evco_part_number": "9475226", "customer_number": "11762"})

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_sales_order_lines_nest_detail_and_releases_for_every_customer_and_status():
    iqms = MagicMock()
    iqms.get_sales_orders.return_value = [
        {"Id": 1, "OrdDetailId": 2, "ArInvtId": 3, "ItemNumber": "EVCO-1", "CustomerNumber": "C1",
         "Company": "Acme", "OrderNumber": "O1", "Status": "Active", "TotalQTYOrdered": 10, "UnitPrice": 2.5},
        {"Id": 4, "OrdDetailId": 5, "ArInvtId": 3, "ItemNumber": "EVCO-1", "CustomerNumber": "C2",
         "Company": "Beta", "OrderNumber": "O2", "Status": "Closed", "TotalQTYOrdered": 5, "UnitPrice": 3.0},
        {"Id": 6, "OrdDetailId": 7, "ArInvtId": 8, "ItemNumber": "OTHER", "CustomerNumber": "C3"},
    ]
    iqms.get_sales_order_details.side_effect = lambda order_id: {
        1: [{"Id": 2, "SalesOrderId": 1, "ArInvtId": 3, "BlanketQty": 10, "UnitPrice": 2.5, "ListUnitPrice": 2.5,
             "UOM": "EACH", "OnHold": True, "ShipHold": False, "Discount": 0, "DropShip": False}],
        4: [{"Id": 5, "SalesOrderId": 4, "ArInvtId": 3, "BlanketQty": 5, "UnitPrice": 3.0, "ListUnitPrice": 3.0,
             "UOM": "EACH", "OnHold": False, "ShipHold": False, "Discount": 0, "DropShip": False}],
    }.get(order_id, [])
    iqms.get_sales_order_releases.side_effect = lambda detail_id: [
        {"Id": 9, "SalesOrderDetailId": detail_id, "ArInvtId": 3, "Seq": 1, "Qty": 10, "OriginalQty": 10,
         "ShippedQty": 0, "LeftToShip": 10}
    ]
    service = SalesOrderService(iqms_client=iqms, sync_repository=MagicMock(),
                                pricing_history_repository=MagicMock(), resolution_repository=MagicMock())

    lines = service.get_sales_order_lines("EVCO-1")

    assert [(line.customer_name, line.status) for line in lines] == [("Acme", "Active"), ("Beta", "Closed")]
    assert lines[0].detail.on_hold is True
    assert [release.release_id for release in lines[0].releases] == [9]
    assert lines[1].detail.sales_order_detail_id == 5


def test_sales_order_lines_empty_returns_empty_list_on_the_route(monkeypatch):
    from app.modules.sales_order import router as so_router

    monkeypatch.setattr(so_router.sales_order_service, "get_sales_order_lines", lambda item_number: [])

    response = client.post("/sales-orders/get-sales-order-lines", json={"evco_part_number": "NOPE"})

    assert response.status_code == 200
    assert response.json()["data"] == {"lines": []}


def _row(line_number, area, **extra):
    base = {
        "review_row_id": uuid4(), "line_item_id": uuid4(), "line_number": line_number, "review_area": area,
        "evco_part_number": "P", "mold_number": None, "manufacturing_bom_number": None, "customer_number": "C",
        "created_at": datetime.now(timezone.utc), "rule_code": "VR-001", "check_name": "check",
        "status": "UPDATE_REQUIRED", "message": "m", "field": None, "record_ref": None, "sales_order_id": None,
        "sales_order_detail_id": None, "release_id": None, "quote_value": None, "system_value": None,
        "proposed_value": None, "exception_id": None,
    }
    base.update(extra)
    return base


def test_review_rows_in_summary_are_sorted_by_line_then_area():
    repo = MagicMock()
    repo.get_rows_for_processing.return_value = [
        _row(2, "SALES_ORDER"), _row(1, "SALES_ORDER"), _row(2, "AKA"), _row(1, "PRICING"), _row(1, "AKA"),
    ]
    service = ReportingService(
        line_item_audit_repository=MagicMock(), aka_resolution_repository=MagicMock(),
        pricing_resolution_repository=MagicMock(), sales_order_resolution_repository=MagicMock(),
        pricing_history_repository=MagicMock(), review_row_repository=repo,
    )

    rows = service._build_review_rows(uuid4())

    assert [(row.line_number, row.review_area) for row in rows] == [
        (1, "AKA"), (1, "PRICING"), (1, "SALES_ORDER"), (2, "AKA"), (2, "SALES_ORDER"),
    ]
    assert rows[0].check == "check"


def test_review_rows_summary_is_empty_when_none_stored():
    repo = MagicMock()
    repo.get_rows_for_processing.return_value = []
    service = ReportingService(
        line_item_audit_repository=MagicMock(), aka_resolution_repository=MagicMock(),
        pricing_resolution_repository=MagicMock(), sales_order_resolution_repository=MagicMock(),
        pricing_history_repository=MagicMock(), review_row_repository=repo,
    )
    assert service._build_review_rows(uuid4()) == []
