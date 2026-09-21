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
    payload = {"Item #": "9480026", "customer#": "10329", "mfg#": "6601/9480026-CHIMEI"}
    response = client.post("/inventory/get-aka", json=payload)
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"
    assert data["error"]["category"] == "SECURITY"


def test_get_aka_known_and_unseeded():
    # 1. Known seeded record - from evco_test_data/mock_data/aka_inventory.json
    # (built from the real dummy quote PDFs).
    payload = {"Item #": "9480026", "customer#": "10329", "mfg#": "6601/9480026-CHIMEI"}
    response = client.post("/inventory/get-aka", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["statusCode"] == 200
    assert data["message"] == "AKA search completed successfully"
    assert data["data"]["Header"]["Item #"] == "9480026"
    detail = data["data"]["akaDetails"][0]
    assert detail["customer#"] == "10329"
    assert detail["mfg#"] == "6601/9480026-CHIMEI"
    assert detail["akaItem#"] == "38795"

    # 2. Unseeded combination returns 404 (no dynamic-fallback placeholder).
    unseeded = {"Item #": "9480026", "customer#": "99999", "mfg#": "DOES-NOT-EXIST"}
    response2 = client.post("/inventory/get-aka", json=unseeded)
    assert response2.status_code == 404
    assert response2.json()["error"]["code"] == "AKA_RECORD_NOT_FOUND"


def test_create_aka_success_and_duplicate():
    create_payload = {
        "Item #": "9999999",
        "createAkaDetails": {
            "akaItem#": "NEW-PART-XYZ",
            "akaDescription": "NEW PART DESCRIPTION",
            "rev": "A",
            "customer#": "CUST-NEW-100",
            "currency": "US Dollar",
            "customername": "NEW TEST CUSTOMER",
            "mfg#": "MFG-NEW-100",
            "shipToAttn": "",
            "minimumSellingQty": 50,
            "sellingMultiplesOf": 25,
        },
    }

    # 1. Create AKA record
    response = client.post("/inventory/create-aka", json=create_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["statusCode"] == 201
    assert data["message"] == "AKA mapping created successfully"
    assert data["data"]["Header"]["Item #"] == "9999999"
    detail = data["data"]["akaDetails"][0]
    assert detail["akaItem#"] == "NEW-PART-XYZ"
    assert detail["customer#"] == "CUST-NEW-100"
    assert detail["mfg#"] == "MFG-NEW-100"

    # 2. Duplicate creation attempt (same Item #/customer#/mfg#) returns 409
    dup_res = client.post("/inventory/create-aka", json=create_payload)
    assert dup_res.status_code == 409
    assert dup_res.json()["statusCode"] == 409


def test_update_aka_known_and_unseeded():
    # 1. Update a known seeded record.
    update_payload = {
        "Item #": "9480026",
        "customer#": "10329",
        "mfg#": "6601/9480026-CHIMEI",
        "updateAkaDetails": {
            "akaDescription": "ACCESS BOX ASSY-Chimei UPDATED",
            "minimumSellingQty": 999,
        },
    }
    response = client.post("/inventory/update-aka", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["statusCode"] == 200
    detail = data["data"]["akaDetails"][0]
    assert detail["akaDescription"] == "ACCESS BOX ASSY-Chimei UPDATED"
    assert detail["minimumSellingQty"] == 999
    # akaItem# untouched since this update didn't set it
    assert detail["akaItem#"] == "38795"

    # 2. Updating an unseeded combination returns 404 (no dynamic-fallback creation).
    unseeded_update = {
        "Item #": "ITEM-ARB-555",
        "customer#": "CUST-ARBITRARY-555",
        "mfg#": "MFG-ARB-555",
        "updateAkaDetails": {"minimumSellingQty": 99},
    }
    response2 = client.post("/inventory/update-aka", json=unseeded_update)
    assert response2.status_code == 404
    assert response2.json()["error"]["code"] == "AKA_RECORD_NOT_FOUND"
