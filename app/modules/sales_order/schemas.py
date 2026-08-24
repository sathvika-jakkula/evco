from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# --- API 1: Get Sales Orders ---
# IQMS's own `filters` query param does not filter server-side, so this is
# filtered against the full sales order list in SalesOrderService.
class GetSalesOrdersRequest(BaseModel):
    item_number: str = Field(..., description="EVCO Item Number to filter sales order lines by")


class SalesOrderData(BaseModel):
    sales_order_id: int = Field(..., description="Sales order header id - feeds get-sales-order-details")
    sales_order_detail_id: int = Field(..., description="Sales order detail line id - feeds get-sales-order-releases")
    ar_invt_id: int
    order_number: str
    po_number: Optional[str] = None
    customer_number: str
    company: str
    item_number: str
    description: Optional[str] = None
    customer_item_number: Optional[str] = None
    customer_description: Optional[str] = None
    status: Optional[str] = None
    rev: Optional[str] = None
    total_qty_ordered: float
    unit_price: float
    date_taken: Optional[datetime] = None
    delivery_date: Optional[datetime] = None


# --- API 2: Get Sales Order Details ---
class GetSalesOrderDetailsRequest(BaseModel):
    sales_order_id: int = Field(..., description="sales_order_id from a prior get-sales-orders call")
    ar_invt_id: int = Field(..., description="ar_invt_id from a prior get-sales-orders call, to isolate one line item")


class SalesOrderDetailData(BaseModel):
    sales_order_detail_id: int = Field(..., description="Feeds get-sales-order-releases")
    sales_order_id: int
    ar_invt_id: int
    blanket_qty: float
    unit_price: float
    list_unit_price: float
    uom: str
    on_hold: bool
    ship_hold: bool
    discount: float
    containers: Optional[float] = None
    drop_ship: bool
    po_info: Optional[str] = None
    note: Optional[str] = None


# --- API 3: Get Sales Order Releases ---
class GetSalesOrderReleasesRequest(BaseModel):
    sales_order_detail_id: int = Field(..., description="sales_order_detail_id from a prior get-sales-order-details call")


class SalesOrderReleaseData(BaseModel):
    release_id: int
    sales_order_detail_id: int
    ar_invt_id: int
    seq: int
    qty: float
    original_qty: float
    shipped_qty: float
    left_to_ship: float
    request_date: Optional[datetime] = None
    promise_date: Optional[datetime] = None
    must_ship_date: Optional[datetime] = None
    ship_date: Optional[datetime] = None
    forecast: Optional[str] = None
    date_type: Optional[str] = None
    acknowledged: bool = False
    expedite: bool = False
