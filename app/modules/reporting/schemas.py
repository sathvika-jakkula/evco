"""
Response shapes for the Quote Execution Summary data API. This module
returns structured data only - no email subject/body text is generated
here; the calling agent formats and sends the report itself.

No mold-check or MOQ-check fields exist anywhere below - no implemented
check exists for either in this codebase, so the API never claims one.
"""

from typing import List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError


class QuoteSummaryRequest(BaseModel):
    processing_id: UUID = Field(..., description="processing_id from a prior /api/extract-quote call")


class ClientReportSource(BaseModel):
    scan_ids: List[UUID] = Field(
        default_factory=list,
        description="Scan run(s). Every quote processed from these scans is included.",
    )
    processing_ids: List[UUID] = Field(
        default_factory=list,
        description="processing_id(s) to include, in addition to any from scan_ids. One sheet per quote.",
    )

    @model_validator(mode="after")
    def _needs_a_source(self):
        if not self.scan_ids and not self.processing_ids:
            raise PydanticCustomError("missing_report_source", "Provide scan_ids, processing_ids, or both")
        return self


class ClientReportRequest(ClientReportSource):
    pass


class AkaResolution(BaseModel):
    decision: Optional[str] = Field(None, description="NOT_FOUND (no AKA found) | CREATED (new AKA record) | NO_CHANGE (already matched) | UPDATED (AKA details changed)")
    aka_id: Optional[str] = Field(None, description="The AKA number/customer part number")
    before: Optional[dict] = Field(None, description="Previous AKA details (if UPDATED)")
    after: Optional[dict] = Field(None, description="Current AKA details (moq, sellingMultiplesOf, etc.)")


class PricingTier(BaseModel):
    quantity: int = Field(..., description="Minimum quantity for this price break")
    previous_price: Optional[float] = Field(None, description="Price before this change (if updated)")
    current_price: float = Field(..., description="Current price at this quantity")
    is_active: bool = Field(..., description="Whether this tier is currently active")
    inactive_date: Optional[str] = Field(None, description="When this tier was deactivated")
    created_by_line_item_id: Optional[UUID] = Field(None, description="Line item that created this tier")
    inactivated_by_line_item_id: Optional[UUID] = Field(None, description="Line item that deactivated this tier")


class PricingResolution(BaseModel):
    decision: Optional[str] = Field(None, description="CREATED (new pricing) | NO_CHANGE (unchanged) | UPDATED (price modified)")
    tiers: List[PricingTier] = Field(default_factory=list, description="All quantity break tiers with price history (each tier shows previous_price and current_price)")


class SalesOrderFact(BaseModel):
    sales_order_id: Optional[str] = Field(None, description="Sales order number")
    decision: Optional[str] = Field(None, description="NO_CHANGE (order price matches current quote) | MISMATCH (order price differs from quote) | NOT_FOUND (no order)")
    note: Optional[str] = Field(None, description="Details about the price comparison (e.g., 'Order $1.20 vs Quote $1.45')")


class SalesOrderResolution(BaseModel):
    decision: Optional[str] = Field(None, description="Overall: NO_CHANGE (all orders match OR no orders) | MISMATCH (at least one order has price difference) | NOT_FOUND (no orders exist)")
    orders: List[SalesOrderFact] = Field(default_factory=list, description="All sales orders for this line item")


class ExceptionDetail(BaseModel):
    exception_code: str
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    message: Optional[str] = None
    is_retryable: Optional[bool] = None
    created_time: Optional[str] = None


class LineItemSummary(BaseModel):
    line_item_id: UUID
    line_number: Optional[int] = Field(None, description="Line number on the quote PDF")
    evco_mfg_number: Optional[str] = Field(None, description="bom_id / EVCO MFG number")
    evco_part_id: Optional[str] = None
    customer_part_number: Optional[str] = None
    line_status: Optional[str] = None
    aka: AkaResolution
    pricing: PricingResolution
    sales_order: SalesOrderResolution
    exceptions: List[str] = Field(default_factory=list)
    exception_details: List[ExceptionDetail] = Field(default_factory=list)


class ReviewRowEntry(BaseModel):
    review_row_id: UUID
    line_item_id: UUID
    line_number: Optional[int] = None
    review_area: str
    evco_part_number: Optional[str] = None
    mold_number: Optional[str] = None
    manufacturing_bom_number: Optional[str] = None
    customer_number: Optional[str] = None
    created_at: datetime
    rule_code: str
    check: str
    status: str
    message: str
    field: Optional[str] = None
    record_ref: Optional[str] = None
    sales_order_id: Optional[str] = None
    sales_order_detail_id: Optional[str] = None
    release_id: Optional[str] = None
    quote_value: Optional[str] = None
    system_value: Optional[str] = None
    proposed_value: Optional[str] = None
    exception_id: Optional[UUID] = None


class QuoteSummaryData(BaseModel):
    processing_id: UUID
    scan_id: Optional[int] = Field(None, description="The scan that picked up this file")
    quote_number: Optional[str] = None
    customer_name: Optional[str] = None
    ar_cust_id: Optional[str] = None
    effective_date: Optional[str] = None
    report_id: Optional[str] = None
    status: Optional[str] = None
    total_lines: int = 0
    successful_lines: int = 0
    review_required_lines: int = 0
    failed_lines: int = 0
    not_processed_lines: int = 0
    exception_count: int = 0
    quote_exceptions: List[ExceptionDetail] = Field(default_factory=list)
    line_items: List[LineItemSummary] = Field(default_factory=list)
    review_rows: List[ReviewRowEntry] = Field(default_factory=list, description="Rows stored by the review agents, sorted by line then area (AKA, PRICING, SALES_ORDER)")
