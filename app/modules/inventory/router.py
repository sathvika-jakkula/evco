from fastapi import APIRouter, Response, status
from app.modules.inventory.schemas import (
    AkaSearchResponse,
    CreateAkaRequest,
    GetAkaRequest,
    StandardInventoryResponse,
    UpdateAkaRequest,
)
from app.modules.inventory.store import inventory_store

router = APIRouter(prefix="/inventory", tags=["Inventory AKA Workflow"])


@router.post(
    "/get-aka",
    response_model=StandardInventoryResponse[AkaSearchResponse],
    summary="T6 Get AKA",
    description=(
        "Get the AKA mapping for an item, identified by evco_part_number, customer_number, "
        "and manufacturing_bom_number. "
        "Returns 200 with full data on a full match; "
        "206 with header only (aka_details=[]) when the item exists but no AKA matches the customer/mfg#; "
        "404 when the item number itself is not found."
    ),
)
async def get_aka(payload: GetAkaRequest, response: Response):
    result = inventory_store.get_aka(
        item_number=payload.item_number,
        customer_number=payload.customer_number,
        manufacturing_bom_number=payload.manufacturing_bom_number,
        line_item_id=payload.line_item_id,
    )

    # Case 2: item found but no matching AKA record for this customer/mfg#
    if not result.aka_details:
        response.status_code = status.HTTP_206_PARTIAL_CONTENT
        return StandardInventoryResponse(
            statusCode=206,
            message=(
                f"Item '{payload.item_number}' found but no AKA record matches "
                f"customer_number '{payload.customer_number}', mfg# '{payload.manufacturing_bom_number}'"
            ),
            data=result,
        )

    # Case 3: full match
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA search completed successfully",
        data=result,
    )


@router.post(
    "/create-aka",
    response_model=StandardInventoryResponse[AkaSearchResponse],
    summary="T7 Create AKA",
    description="Create a new AKA mapping record under an evco_part_number",
)
async def create_aka(payload: CreateAkaRequest, response: Response):
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
    "/reset",
    summary="Reset AKA store (testing only)",
    description="Wipes the in-memory AKA store and re-seeds it from aka_inventory.json. Use this between E2E test runs to restore original state.",
)
async def reset_aka_store():
    result = inventory_store.reset()
    return {
        "message": "AKA store reset to original seed data",
        "seeded_items": result["items"],
        "seeded_aka_records": result["aka_records"],
    }


@router.post(
    "/update-aka",
    response_model=StandardInventoryResponse[AkaSearchResponse],
    summary="T8 Update AKA",
    description="Update the AKA mapping identified by evco_part_number, customer_number, and manufacturing_bom_number",
)
async def update_aka(payload: UpdateAkaRequest, response: Response):
    result = inventory_store.update_aka(payload)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA record updated successfully",
        data=result,
    )
