from fastapi import APIRouter, Response, status
from app.modules.inventory.schemas import (
    AkaSearchData,
    CreateAkaRequest,
    GetAkaRequest,
    StandardInventoryResponse,
    UpdateAkaRequest,
)
from app.modules.inventory.store import inventory_store

router = APIRouter(prefix="/inventory", tags=["Inventory AKA Workflow"])


@router.post(
    "/get-aka",
    response_model=StandardInventoryResponse[AkaSearchData],
    summary="T6 Get AKA",
    description="Get the AKA mapping for an item, identified by Item #, customer#, and mfg#",
)
async def get_aka(payload: GetAkaRequest, response: Response):
    result = inventory_store.get_aka(
        item_number=payload.item_number,
        customer_number=payload.customer_number,
        manufacturing_bom_number=payload.manufacturing_bom_number,
        line_item_id=payload.line_item_id,
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA search completed successfully",
        data=result,
    )


@router.post(
    "/create-aka",
    response_model=StandardInventoryResponse[AkaSearchData],
    summary="T7 Create AKA",
    description="Create a new AKA mapping record under an Item #",
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
    "/update-aka",
    response_model=StandardInventoryResponse[AkaSearchData],
    summary="T8 Update AKA",
    description="Update the AKA mapping identified by Item #, customer#, and mfg#",
)
async def update_aka(payload: UpdateAkaRequest, response: Response):
    result = inventory_store.update_aka(payload)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA record updated successfully",
        data=result,
    )
