from fastapi import APIRouter, Response, status
from starlette.responses import JSONResponse

from app.core.exceptions import BusinessException
from app.core.config import settings
from app.modules.inventory.schemas import (
    AkaLiveDetail,
    AkaLiveHeader,
    AkaLiveSearchData,
    AkaSearchData,
    AkaSearchResponse,
    CreateAkaRequest,
    GetAkaRequest,
    StandardInventoryResponse,
    UpdateAkaRequest,
)
from app.modules.inventory.service import inventory_service
from app.modules.inventory.store import inventory_store

router = APIRouter(prefix="/inventory", tags=["Inventory AKA Workflow"])


def _to_live_aka(result: AkaSearchData) -> AkaLiveSearchData:
    """Only the fields the IQMS WebAPI returns. Empty values are left out of the JSON
    (response_model_exclude_none), so no placeholder is sent for a field IQMS does not have."""
    return AkaLiveSearchData(
        header=AkaLiveHeader(
            evco_part_number=result.header.item_number,
            rev=result.header.rev or None,
            part_description=result.header.description or None,
        ),
        aka_details=[
            AkaLiveDetail(
                customer_part_number=detail.aka_item_number,
                part_description=detail.aka_description or None,
                rev=detail.rev or None,
                customer_number=detail.customer_number,
                customer_name=detail.customer_name or None,
                ship_to_attn=detail.ship_to_attn or None,
            )
            for detail in result.aka_details
        ],
    )


@router.post(
    "/get-aka",
    response_model=StandardInventoryResponse[AkaLiveSearchData],
    summary="T6 Get AKA",
    description=(
        "AKA records for a part and customer, from the IQMS WebAPI. Always 200. "
        "header is null when no AKA record is found for the part, and aka_details is [] when the "
        "customer has no AKA for the part. Fields IQMS does not return are omitted."
    ),
)
def get_aka(payload: GetAkaRequest):
    try:
        if settings.IQMS_USE_FIXTURES:
            result = inventory_store.get_aka(
                item_number=payload.item_number,
                customer_number=payload.customer_number,
                manufacturing_bom_number=None,
                line_item_id=payload.line_item_id,
            )
        else:
            result = inventory_service.get_aka_search(
                item_number=payload.item_number,
                customer_number=payload.customer_number,
                line_item_id=payload.line_item_id,
            )
    except BusinessException as exc:
        # A part or customer that IQMS does not know is a business outcome, not a request error.
        if exc.code not in ("ITEM_NOT_FOUND", "CUSTOMER_NOT_FOUND"):
            raise
        result = None

    live = _to_live_aka(result) if result is not None else AkaLiveSearchData(header=None, aka_details=[])
    found = bool(live.aka_details)
    data = {
        "header": live.header.model_dump(exclude_none=True) if found and live.header else None,
        "aka_details": [detail.model_dump(exclude_none=True) for detail in live.aka_details],
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "statusCode": 200,
            "message": "AKA search completed successfully" if found else
                       "No AKA record found for this part and customer",
            "data": data,
            "error": None,
        },
    )


@router.post(
    "/create-aka/dummy",
    response_model=StandardInventoryResponse[AkaSearchResponse],
    summary="T7 Create AKA",
    description="Create a new AKA mapping record under an evco_part_number",
)
def create_aka(payload: CreateAkaRequest, response: Response):
    result = inventory_store.create_aka(payload)
    if not result:
        response.status_code = status.HTTP_409_CONFLICT
        return StandardInventoryResponse(
            statusCode=409,
            message="AKA record already exists",
            data=None,
        )

    response.status_code = status.HTTP_201_CREATED
    return StandardInventoryResponse(
        statusCode=201,
        message="AKA mapping created successfully",
        data=result,
    )


@router.post(
    "/reset/dummy",
    summary="Reset AKA store (testing only)",
    description="Wipes the in-memory AKA store and re-seeds it from aka_inventory.json. Use this between E2E test runs to restore original state.",
)
def reset_aka_store():
    result = inventory_store.reset()
    return {
        "message": "AKA store reset to original seed data",
        "seeded_items": result["items"],
        "seeded_aka_records": result["aka_records"],
    }


@router.post(
    "/update-aka/dummy",
    response_model=StandardInventoryResponse[AkaSearchResponse],
    summary="T8 Update AKA",
    description="Update the AKA mapping identified by evco_part_number, customer_number, and manufacturing_bom_number",
)
def update_aka(payload: UpdateAkaRequest, response: Response):
    result = inventory_store.update_aka(payload)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA record updated successfully",
        data=result,
    )
