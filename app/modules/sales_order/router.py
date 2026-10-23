from fastapi import APIRouter, Response, status

from app.modules.inventory.schemas import StandardInventoryResponse
from app.modules.sales_order.schemas import (
    GetSalesOrderDetailsRequest,
    GetSalesOrderReleasesRequest,
    GetSalesOrdersRequest,
    SalesOrderData,
    SalesOrderDetailData,
    SalesOrderReleaseData,
    UpdateSalesOrderDetailsRequest,
    UpdateSalesOrderDetailsResponse,
)
from app.modules.sales_order.service import sales_order_service

router = APIRouter(prefix="/sales-orders", tags=["Sales Orders"])


@router.post(
    "/get-sales-orders",
    response_model=StandardInventoryResponse[list[SalesOrderData]],
    summary="Get Sales Orders",
    description=(
        "Retrieve sales order line records matching the given evco_part_number. IQMS's own "
        "filters query parameter does not filter server-side, so this fetches the "
        "full sales order list and filters by evco_part_number here."
    ),
)
def get_sales_orders(payload: GetSalesOrdersRequest, response: Response):
    records = sales_order_service.get_sales_orders(item_number=payload.item_number, line_item_id=payload.line_item_id)
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Sales orders retrieved successfully",
        data=records,
    )


@router.post(
    "/get-sales-order-details",
    response_model=StandardInventoryResponse[list[SalesOrderDetailData]],
    summary="Get Sales Order Details",
    description="Retrieve detail (line item) records for a given sales_order_id and ar_invt_id.",
)
def get_sales_order_details(payload: GetSalesOrderDetailsRequest, response: Response):
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
    response_model=StandardInventoryResponse[list[SalesOrderReleaseData]],
    summary="Get Sales Order Releases",
    description="Retrieve the release/shipment schedule for a given sales_order_detail_id.",
)
def get_sales_order_releases(payload: GetSalesOrderReleasesRequest, response: Response):
    records = sales_order_service.get_sales_order_releases(
        sales_order_detail_id=payload.sales_order_detail_id, line_item_id=payload.line_item_id
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Sales order releases retrieved successfully",
        data=records,
    )


@router.post(
    "/update-sales-order-details",
    response_model=StandardInventoryResponse[UpdateSalesOrderDetailsResponse],
    summary="Update Sales Order Details",
    description="Update price, on_hold, and ship_hold flags for a sales order detail line.",
    status_code=status.HTTP_200_OK,
)
def update_sales_order_details(payload: UpdateSalesOrderDetailsRequest, response: Response):
    result = sales_order_service.update_sales_order_details(
        sales_order_detail_id=payload.sales_order_detail_id,
        unit_price=payload.unit_price,
        on_hold=payload.on_hold,
        ship_hold=payload.ship_hold,
        line_item_id=payload.line_item_id,
    )
    response.status_code = status.HTTP_200_OK
    return StandardInventoryResponse(
        statusCode=200,
        message="Sales order details updated successfully",
        data=result,
    )
