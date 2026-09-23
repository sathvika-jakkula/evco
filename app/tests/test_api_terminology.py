"""Contract checks for extraction vocabulary at the HTTP boundary."""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.inventory import router as inventory_router
from app.modules.inventory.schemas import AkaDetail
from app.modules.pricing import router as pricing_router
from app.modules.pricing.service import PriceBreakService
from app.modules.sales_order import router as sales_router


@pytest.fixture
def api(monkeypatch):
    service = PriceBreakService(
        pricing_history_repository=MagicMock(),
        pricing_resolution_repository=MagicMock(),
        processing_repository=MagicMock(),
    )
    monkeypatch.setattr(pricing_router, "price_break_service", service)
    app = FastAPI()
    for router in (inventory_router.router, pricing_router.router, sales_router.router):
        app.include_router(router)
    return TestClient(app), service


@pytest.mark.parametrize("legacy", [False, True])
def test_pricing_lookup_add_update_contract(api, legacy):
    client, service = api
    context = {"evco_part_number": "9480026", "customer_number": "10329",
               "manufacturing_bom_number": "9440502 + 9440503"}
    if legacy:
        context = {"Item #": "9480026", "customer#": "10329", "mfg#": "9440502 + 9440503"}
    response = client.post("/inventory/get-pricebreaks", json=context)
    assert response.status_code == 200
    assert set(response.json()["data"][0]) == {"moq", "price", "comment"}

    payload = {"moq": 123456, "price": 1.5,
               "price_effective_date": "2026-10-01T00:00:00Z", "quote_number": "Q-123"}
    if legacy:
        payload = {"quantity": 123456, "price": 1.5,
                   "effective_date": "2026-10-01T00:00:00Z", "source_quote_number": "Q-123"}
    payload.update(context)
    service._pricing_history_repository.get_active_price.return_value = None
    response = client.post("/inventory/add-pricebreak", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["moq"] == 123456
    assert data["price"] == 1.5
    assert data["price_effective_date"] == "2026-10-01T00:00:00Z"
    assert not {"quantity", "unit_price", "effective_date"}.intersection(data)
    persisted = service._pricing_history_repository.record_price_change.call_args.kwargs
    assert persisted["quantity"] == 123456
    assert persisted["source_quote_number"] == "Q-123"
    assert persisted["evco_part_number"] == "9480026"

    payload["price"] = 2.5
    response = client.post("/inventory/update-pricebreak", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["price"] == 2.5
    record = next(r for r in service._current_price_breaks if r["quantity"] == 123456)
    assert record["unit_price"] == 2.5


def test_numeric_validation_remains_in_place(api):
    client, service = api
    before = list(service._current_price_breaks)
    response = client.post("/inventory/add-pricebreak", json={
        "moq": 0, "price": -1, "price_effective_date": "2026-10-01T00:00:00Z",
    })
    assert response.status_code == 422
    assert service._current_price_breaks == before


def test_inventory_audit_serialization_keeps_legacy_names():
    detail = AkaDetail.model_validate({
        "customer_part_number": "CUSTOMER-PART", "customer_number": "C1",
        "manufacturing_bom_number": "BOM1", "moq": 100,
        "box_quantity": 25, "mold_number": "M1",
    })
    stored = detail.model_dump(by_alias=True)
    assert stored["akaItem#"] == "CUSTOMER-PART"
    assert stored["minimumSellingQty"] == 100
    assert stored["sellingMultiplesOf"] == 25
    assert stored["soItemNumber"] == "M1"


def test_openapi_advertises_canonical_requests_and_typed_responses(api):
    client, _ = api
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("GetAkaRequest", "GetPriceBreaksRequest", "GetSalesOrdersRequest"):
        assert "evco_part_number" in schemas[name]["properties"]
        assert "Item #" not in schemas[name]["properties"]
        assert "item_number" not in schemas[name]["properties"]
    for name in ("CreateAkaDetails", "UpdateAkaDetails"):
        assert {"customer_part_number", "part_description", "moq", "box_quantity", "mold_number"} <= schemas[name]["properties"].keys()
    for name in ("AddPriceBreakRequest", "UpdatePriceBreakRequest"):
        assert {"moq", "price", "price_effective_date", "quote_number"} <= schemas[name]["properties"].keys()
    assert set(schemas["PriceBreakData"]["properties"]) == {"moq", "price", "comment"}
    for path, item in spec["paths"].items():
        response = item["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        envelope = schemas[response["$ref"].split("/")[-1]]
        assert envelope["properties"]["data"]["anyOf"][0] != {}


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_inventory_request_schema_modes_use_canonical_names(mode):
    from app.modules.inventory.schemas import (
        CreateAkaRequest, GetAkaRequest, UpdateAkaRequest,
    )

    for model in (GetAkaRequest, CreateAkaRequest, UpdateAkaRequest):
        schema = model.model_json_schema(mode=mode)
        assert "evco_part_number" in schema["properties"]
        assert "Item #" not in schema["properties"]
        for definition in [schema, *schema.get("$defs", {}).values()]:
            assert not {
                "customer#", "mfg#", "akaItem#", "akaDescription",
                "createAkaDetails", "updateAkaDetails", "minimumSellingQty",
                "sellingMultiplesOf", "soItemNumber",
            }.intersection(definition.get("properties", {}))
