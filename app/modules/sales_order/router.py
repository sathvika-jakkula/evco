from fastapi import APIRouter, Response, status

from app.modules.inventory.schemas import StandardInventoryResponse
from app.modules.sales_order.schemas import (
    GetSalesOrderDetailsRequest,
    GetSalesOrderReleasesRequest,
    GetSalesOrdersRequest,
)
from app.modules.sales_order.service import sales_order_service

router = APIRouter(prefix="/sales-orders", tags=["Sales Orders"])


@router.post(
    "/get-sales-orders",
    response_model=StandardInventoryResponse,
    summary="Get Sales Orders",
    description=(
        "Retrieve sales order line records matching the given item_number. IQMS's own "
        "filters query parameter does not filter server-side, so this fetches the "
        "full sales order list and filters by item_number here."
    ),
)
async def get_sales_orders(payload: GetSalesOrdersRequest, response: Response):
    records = sales_order_service.get_sales_orders(item_number=payload.item_number)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Sales orders retrieved successfully",
        data=records,
    )


@router.post(
    "/get-sales-order-details",
    response_model=StandardInventoryResponse,
    summary="Get Sales Order Details",
    description="Retrieve detail (line item) records for a given sales_order_id and ar_invt_id.",
)
async def get_sales_order_details(payload: GetSalesOrderDetailsRequest, response: Response):
    records = sales_order_service.get_sales_order_details(
        sales_order_id=payload.sales_order_id,
        ar_invt_id=payload.ar_invt_id,
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Sales order details retrieved successfully",
        data=records,
    )


@router.post(
    "/get-sales-order-releases",
    response_model=StandardInventoryResponse,
    summary="Get Sales Order Releases",
    description="Retrieve the release/shipment schedule for a given sales_order_detail_id.",
)
async def get_sales_order_releases(payload: GetSalesOrderReleasesRequest, response: Response):
    records = sales_order_service.get_sales_order_releases(
        sales_order_detail_id=payload.sales_order_detail_id
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Sales order releases retrieved successfully",
        data=records,
    )
