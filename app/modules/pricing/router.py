from fastapi import APIRouter, Response, status

from app.modules.inventory.schemas import StandardInventoryResponse
from app.modules.pricing.schemas import (
    AddPriceBreakRequest,
    GetPriceBreaksRequest,
    UpdatePriceBreakRequest,
)
from app.modules.pricing.service import price_break_service

# No IQMS internal IDs (arinvt_id / arCustoId / priceBreakId) are ever
# accepted. The RPA operates on the customer/item context already open in
# IQMS and identifies the relevant price break itself; the backend only
# ever supplies/receives business data.
router = APIRouter(prefix="/inventory", tags=["Inventory Price Breaks"])


@router.post(
    "/get-pricebreaks",
    response_model=StandardInventoryResponse,
    summary="Get Price Breaks",
    description="Retrieve all price breaks for the current customer/item context (no IDs are accepted)",
)
async def get_pricebreaks(payload: GetPriceBreaksRequest, response: Response):
    price_breaks = price_break_service.get_price_breaks(
        customer_number=payload.customer_number,
        evco_part_number=payload.evco_part_number,
        bom_number=payload.manufacturing_bom_number,
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Price breaks retrieved successfully",
        data=price_breaks,
    )


@router.post(
    "/add-pricebreak",
    response_model=StandardInventoryResponse,
    summary="Add Price Break",
    description="Add a new price break tier to the current customer/item context (no IDs are accepted)",
)
async def add_pricebreak(payload: AddPriceBreakRequest, response: Response):
    result = price_break_service.add_price_break(payload)
    response.status_code = status.HTTP_201_CREATED
    return StandardInventoryResponse(
        statusCode=201,
        message="Price break added successfully",
        data=result,
    )


@router.post(
    "/update-pricebreak",
    response_model=StandardInventoryResponse,
    summary="Update Price Break",
    description="Update the price break tier identified by business context (quantity), not a database ID",
)
async def update_pricebreak(payload: UpdatePriceBreakRequest, response: Response):
    result = price_break_service.update_price_break(payload)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Price break updated successfully",
        data=result,
    )
