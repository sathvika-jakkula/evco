from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.exceptions import BusinessException
from app.core.security import validate_access_token
import app.modules.inventory.router as inventory_router

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_auth_dependency():
    """Overrides validate_access_token dependency for inventory API tests."""
    app.dependency_overrides[validate_access_token] = lambda: {
        "sub": "test-user-id",
        "tenant": "test-tenant",
    }
    yield
    app.dependency_overrides.clear()


def test_unauthenticated_request_returns_401():
    """Verify that accessing inventory API without auth override or token returns 401."""
    app.dependency_overrides.clear()
    payload = {"evco_part_number": "453543"}
    response = client.post("/inventory/search-part", json=payload)
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"
    assert data["error"]["category"] == "SECURITY"


def test_search_part_known_and_arbitrary():
    # 1. Known seeded part
    p1 = {"evco_part_number": "453543"}
    r1 = client.post("/inventory/search-part", json=p1)
    assert r1.status_code == 200
    assert r1.json()["data"]["evco_part_number"] == "453543"

    # 2. Arbitrary part number searched dynamically returns requested part number
    p2 = {"evco_part_number": "CUSTOM-PART-9999"}
    r2 = client.post("/inventory/search-part", json=p2)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["statusCode"] == 200
    assert d2["message"] == "Inventory part found successfully"
    assert d2["data"]["evco_part_number"] == "CUSTOM-PART-9999"
    assert d2["data"]["item_number"] == "CUSTOM-PART-9999"
    assert "arinvt_id" not in d2["data"]


def test_get_bom_candidates_known_and_arbitrary():
    # 1. Known seeded part
    p1 = {"evco_part_number": "9445626"}
    r1 = client.post("/inventory/get-bom-candidates", json=p1)
    assert r1.status_code == 200
    assert len(r1.json()["data"]) == 2

    # 2. Arbitrary part number returns dynamic candidate echoing part number
    p2 = {"evco_part_number": "ANY-PART-777"}
    r2 = client.post("/inventory/get-bom-candidates", json=p2)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["statusCode"] == 200
    assert len(d2["data"]) == 1
    assert d2["data"][0]["item_number"] == "ANY-PART-777"
    assert d2["data"][0]["manufacturing_bom_number"] == "BOM-8798"
    assert "bomId" not in d2["data"][0]


def test_get_aka_calls_live_iqms_backed_service(monkeypatch):
    """/get-aka is now backed by InventoryService (live IQMS), not the mock store."""
    fake_service = MagicMock()
    fake_service.get_aka.return_value = [
        {
            "customer_number": "10192",
            "customer_part_number": "3412490",
            "item_number": "9451110",
            "aka_description": "COVER, CTRL HOUSING",
            "item_description": "3412490 COVER, CTRL HOUSING SUB-ZERO",
            "uom": "EACH",
            "currency": "USD",
            "manufacturing_bom_number": "N/A",
            "moq": 0,
            "selling_multiples_of": 1,
        }
    ]
    monkeypatch.setattr(inventory_router, "inventory_service", fake_service)

    payload = {
        "customer_number": "10192",
        "customer_part_number": "3412490",
        "item_number": "9451110",
    }
    response = client.post("/inventory/get-aka", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["data"][0]["item_number"] == "9451110"
    assert data["data"][0]["customer_number"] == "10192"
    fake_service.get_aka.assert_called_once_with(
        customer_number="10192",
        customer_part_number="3412490",
        item_number="9451110",
    )


def test_get_aka_customer_not_found_returns_404(monkeypatch):
    fake_service = MagicMock()
    fake_service.get_aka.side_effect = BusinessException(
        message="No IQMS customer found for the given customer_number",
        code="CUSTOMER_NOT_FOUND",
        status_code=404,
        details={"customer_number": "UNKNOWN-999"},
    )
    monkeypatch.setattr(inventory_router, "inventory_service", fake_service)

    response = client.post("/inventory/get-aka", json={
        "customer_number": "UNKNOWN-999",
        "customer_part_number": "X",
        "item_number": "Y",
    })
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_create_aka_success_and_duplicate():
    create_payload = {
        "customer_number": "CUST-NEW-100",
        "aka_item_number": "NEW-PART-XYZ",
        "aka_description": "NEW PART DESCRIPTION",
        "item_number": "453543",
        "item_description": "Flow Control Base Moulded",
        "uom": "EA",
        "currency": "USD",
        "manufacturing_bom_number": "BOM-8798",
        "moq": 50,
        "selling_multiples_of": 25,
    }

    # 1. Create AKA record
    response = client.post("/inventory/create-aka", json=create_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["statusCode"] == 201
    assert data["message"] == "AKA mapping created successfully"
    assert data["data"]["status"] == "CREATED"
    assert data["data"]["customer_number"] == "CUST-NEW-100"
    assert data["data"]["aka_item_number"] == "NEW-PART-XYZ"
    assert data["data"]["item_number"] == "453543"

    # 2. Duplicate creation attempt returns 409
    dup_res = client.post("/inventory/create-aka", json=create_payload)
    assert dup_res.status_code == 409
    dup_data = dup_res.json()
    assert dup_data["statusCode"] == 409


def test_update_aka_known_and_arbitrary():
    # 1. Update known seed record
    update_payload = {
        "customer_number": "CUST-98211",
        "customer_part_number": "H4652 474 BASE",
        "item_number": "9445626",
        "aka_description": "FLOW CONTROL CLACK UPDATED",
        "item_description": "Flow Control Base Moulded",
        "currency": "USD",
        "manufacturing_bom_number": "BOM-8798",
        "moq": 25,
        "selling_multiples_of": 5,
    }

    response = client.post("/inventory/update-aka", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["statusCode"] == 200
    assert data["data"]["before"]["moq"] == 20
    assert data["data"]["after"]["moq"] == 25

    # 2. Update arbitrary unseeded key (dynamically initializes before state and updates after state)
    arbitrary_update = {
        "customer_number": "CUST-ARBITRARY-555",
        "customer_part_number": "PART-ARB-555",
        "item_number": "ITEM-ARB-555",
        "aka_description": "DYNAMIC UPDATED DESC",
        "moq": 99,
    }
    response2 = client.post("/inventory/update-aka", json=arbitrary_update)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["data"]["customer_number"] == "CUST-ARBITRARY-555"
    assert data2["data"]["customer_part_number"] == "PART-ARB-555"
    assert data2["data"]["item_number"] == "ITEM-ARB-555"
    assert data2["data"]["before"]["aka_description"] == "FLOW CONTROL CLACK"
    assert data2["data"]["after"]["aka_description"] == "DYNAMIC UPDATED DESC"
    assert data2["data"]["after"]["moq"] == 99
