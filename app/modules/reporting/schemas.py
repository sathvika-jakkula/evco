"""
Response shapes for the Quote Execution Summary data API. This module
returns structured data only - no email subject/body text is generated
here; the calling agent formats and sends the report itself.

No mold-check or MOQ-check fields exist anywhere below - no implemented
check exists for either in this codebase, so the API never claims one.
"""

from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class QuoteSummaryRequest(BaseModel):
    processing_id: UUID = Field(..., description="processing_id from a prior /api/extract-quote call")


class AkaResolution(BaseModel):
    decision: Optional[str] = Field(None, description="NOT_FOUND | CREATED | NO_CHANGE | UPDATED")
    aka_id: Optional[str] = None
    before: Optional[dict] = None
    after: Optional[dict] = None


class PricingTier(BaseModel):
    quantity: int
    price: float
    is_active: bool
    inactive_date: Optional[str] = None
    created_by_line_item_id: Optional[UUID] = None
    inactivated_by_line_item_id: Optional[UUID] = None


class PricingResolution(BaseModel):
    decision: Optional[str] = Field(None, description="CREATED | NO_CHANGE | UPDATED")
    before_price: Optional[float] = Field(None, description="Price before this change - price only, never mold/MOQ")
    after_price: Optional[float] = Field(None, description="Price after this change - price only, never mold/MOQ")
    tiers: List[PricingTier] = Field(default_factory=list)


class SalesOrderFact(BaseModel):
    sales_order_id: Optional[str] = None
    decision: Optional[str] = Field(None, description="NOT_FOUND | NO_CHANGE | MISMATCH")
    note: Optional[str] = None


class SalesOrderResolution(BaseModel):
    decision: Optional[str] = Field(None, description="Overall decision across this line's sales orders")
    orders: List[SalesOrderFact] = Field(default_factory=list)


class LineItemSummary(BaseModel):
    line_item_id: UUID
    evco_mfg_number: Optional[str] = Field(None, description="bom_id / EVCO MFG number")
    evco_part_id: Optional[str] = None
    customer_part_number: Optional[str] = None
    aka: AkaResolution
    pricing: PricingResolution
    sales_order: SalesOrderResolution
    exceptions: List[str] = Field(default_factory=list)
    line_status: Optional[str] = None


class QuoteSummaryData(BaseModel):
    processing_id: UUID
    quote_number: Optional[str] = None
    status: Optional[str] = None
    effective_date: Optional[str] = None
    customer_name: Optional[str] = None
    ar_cust_id: Optional[str] = None
    report_id: Optional[str] = None
    total_lines: int = 0
    successful_lines: int = 0
    review_required_lines: int = 0
    failed_lines: int = 0
    exception_count: int = 0
    line_items: List[LineItemSummary] = Field(default_factory=list)
