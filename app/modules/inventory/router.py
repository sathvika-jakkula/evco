from fastapi import APIRouter, Response, status
from app.modules.inventory.schemas import (
    CreateAkaRequest,
    GetAkaRequest,
    GetBomCandidatesRequest,
    SearchPartRequest,
    StandardInventoryResponse,
    UpdateAkaRequest,
)
from app.modules.inventory.service import inventory_service
from app.modules.inventory.store import inventory_store

router = APIRouter(prefix="/inventory", tags=["Inventory AKA Workflow"])


@router.post(
    "/search-part",
    response_model=StandardInventoryResponse,
    summary="T4 Search Inventory Part",
    description="Search for an inventory part by EVCO Part Number (returns part details for any searched number)",
)
async def search_part(payload: SearchPartRequest, response: Response):
    part_data = inventory_store.search_part(payload.evco_part_number)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Inventory part found successfully",
        data=part_data,
    )


@router.post(
    "/get-bom-candidates",
    response_model=StandardInventoryResponse,
    summary="T5 Get BOM Candidates",
    description="Retrieve BOM candidates for any given EVCO Part Number",
)
async def get_bom_candidates(payload: GetBomCandidatesRequest, response: Response):
    bom_candidates = inventory_store.get_bom_candidates(payload.evco_part_number)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="BOM candidates retrieved successfully",
        data=bom_candidates,
    )


@router.post(
    "/get-aka",
    response_model=StandardInventoryResponse,
    summary="T6 Get AKA",
    description="Get AKA mapping record by customer_number, customer_part_number, and item_number",
)
async def get_aka(payload: GetAkaRequest, response: Response):
    records = inventory_service.get_aka(
        customer_number=payload.customer_number,
        customer_part_number=payload.customer_part_number,
        item_number=payload.item_number,
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA record found successfully",
        data=records,
    )


@router.post(
    "/create-aka",
    response_model=StandardInventoryResponse,
    summary="T7 Create AKA",
    description="Create a new AKA mapping record",
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
    response_model=StandardInventoryResponse,
    summary="T8 Update AKA",
    description="Update an AKA mapping record and return before/after state",
)
async def update_aka(payload: UpdateAkaRequest, response: Response):
    result = inventory_store.update_aka(payload)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="AKA record updated successfully",
        data=result,
    )
