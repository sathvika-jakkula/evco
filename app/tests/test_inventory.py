import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import validate_access_token

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
    payload = {"evco_part_number": "9480026", "customer_number": "10329", "manufacturing_bom_number": "6601/9480026-CHIMEI"}
    response = client.post("/inventory/get-aka", json=payload)
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"
    assert data["error"]["category"] == "SECURITY"


@pytest.mark.parametrize("legacy", [False, True])
def test_get_aka_known_and_unseeded(legacy):
    # 1. Known seeded record - from evco_test_data/mock_data/aka_inventory.json
    # (built from the real dummy quote PDFs).
    payload = {"evco_part_number": "9480026", "customer_number": "10329", "manufacturing_bom_number": "6601/9480026-CHIMEI"}
    if legacy:
        payload = {"Item #": "9480026", "customer#": "10329", "mfg#": "6601/9480026-CHIMEI"}
    response = client.post("/inventory/get-aka", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["statusCode"] == 200
    assert data["message"] == "AKA search completed successfully"
    assert data["data"]["header"]["evco_part_number"] == "9480026"
    detail = data["data"]["aka_details"][0]
    assert detail["customer_number"] == "10329"
    assert detail["manufacturing_bom_number"] == "6601/9480026-CHIMEI"
    assert detail["customer_part_number"] == "38795"

    # 2. Unseeded combination returns 404 (no dynamic-fallback placeholder).
    unseeded = {"evco_part_number": "9480026", "customer_number": "99999", "manufacturing_bom_number": "DOES-NOT-EXIST"}
    response2 = client.post("/inventory/get-aka", json=unseeded)
    assert response2.status_code == 404
    assert response2.json()["error"]["code"] == "AKA_RECORD_NOT_FOUND"


def test_create_aka_success_and_duplicate():
    create_payload = {
        "evco_part_number": "9999999",
        "create_aka_details": {
            "customer_part_number": "NEW-PART-XYZ",
            "part_description": "NEW PART DESCRIPTION",
            "rev": "A",
            "customer_number": "CUST-NEW-100",
            "currency": "US Dollar",
            "customer_name": "NEW TEST CUSTOMER",
            "manufacturing_bom_number": "MFG-NEW-100",
            "ship_to_attn": "",
            "moq": 50,
            "box_quantity": 25,
            "mold_number": "MOLD-100",
        },
    }

    # 1. Create AKA record
    response = client.post("/inventory/create-aka", json=create_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["statusCode"] == 201
    assert data["message"] == "AKA mapping created successfully"
    assert data["data"]["header"]["evco_part_number"] == "9999999"
    detail = data["data"]["aka_details"][0]
    assert detail["customer_part_number"] == "NEW-PART-XYZ"
    assert detail["moq"] == 50
    assert detail["box_quantity"] == 25
    assert detail["mold_number"] == "MOLD-100"
    assert detail["customer_number"] == "CUST-NEW-100"
    assert detail["manufacturing_bom_number"] == "MFG-NEW-100"

    # 2. Duplicate creation attempt (same Item #/customer#/mfg#) returns 409
    dup_res = client.post("/inventory/create-aka", json=create_payload)
    assert dup_res.status_code == 409
    assert dup_res.json()["statusCode"] == 409


def test_update_aka_known_and_unseeded():
    # 1. Update a known seeded record.
    update_payload = {
        "evco_part_number": "9480026",
        "customer_number": "10329",
        "manufacturing_bom_number": "6601/9480026-CHIMEI",
        "update_aka_details": {
            "part_description": "ACCESS BOX ASSY-Chimei UPDATED",
            "moq": 999,
        },
    }
    response = client.post("/inventory/update-aka", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["statusCode"] == 200
    detail = data["data"]["aka_details"][0]
    assert detail["part_description"] == "ACCESS BOX ASSY-Chimei UPDATED"
    assert detail["moq"] == 999
    # akaItem# untouched since this update didn't set it
    assert detail["customer_part_number"] == "38795"

    # 2. Updating an unseeded combination returns 404 (no dynamic-fallback creation).
    unseeded_update = {
        "evco_part_number": "ITEM-ARB-555",
        "customer_number": "CUST-ARBITRARY-555",
        "manufacturing_bom_number": "MFG-ARB-555",
        "update_aka_details": {"moq": 99},
    }
    response2 = client.post("/inventory/update-aka", json=unseeded_update)
    assert response2.status_code == 404
    assert response2.json()["error"]["code"] == "AKA_RECORD_NOT_FOUND"
