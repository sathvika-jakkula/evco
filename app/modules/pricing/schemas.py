from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field

# No IDs (arinvt_id / arCustoId / priceBreakId) are accepted anywhere in the
# Price Break API - the RPA operates on the customer/item context already
# open in IQMS and identifies the relevant price break itself.


# --- API 1: Get Price Breaks ---
class GetPriceBreaksRequest(BaseModel):
    evco_part_number: str = Field(..., description="EVCO Part Number to retrieve price breaks for")
    customer_number: str = Field(..., description="Customer Number to retrieve price breaks for")
    manufacturing_bom_number: str = Field(..., description="Manufacturing/BOM Number to retrieve price breaks for")
    # Optional linkage for pricing_results/exception_logs persistence only - see
    # AddPriceBreakRequest for the same pattern. Not required so existing callers
    # that omit them keep working unchanged.
    processing_id: Optional[UUID] = Field(default=None, description="quote_processing run this lookup came from, for result/exception persistence")
    line_item_id: Optional[UUID] = Field(default=None, description="quote_line_items row this lookup came from, for result/exception persistence")


class PriceBreakData(BaseModel):
    unit_price: float
    quantity: int
    comment: str


# --- API 2: Add Price Break ---
class AddPriceBreakRequest(BaseModel):
    quantity: int = Field(..., gt=0, description="Quantity threshold for the new price break tier")
    price: float = Field(..., gt=0, description="Unit price for the new price break tier")
    effective_date: datetime = Field(..., description="Date the price break becomes effective")
    # Optional business-key context for pricing_history persistence only (see
    # PriceBreakService/pricing_history_repository) - the RPA-driven price
    # break screen itself still identifies its target purely by quantity, as
    # noted above; these fields do not change that. Not required so existing
    # callers that omit them keep working unchanged.
    evco_part_number: Optional[str] = Field(default=None, description="EVCO Part Number, for pricing history persistence")
    customer_number: Optional[str] = Field(default=None, description="Customer Number, for pricing history persistence")
    manufacturing_bom_number: Optional[str] = Field(default=None, description="Manufacturing/BOM Number, for pricing history persistence")
    currency: Optional[str] = Field(default="USD", description="Currency, for pricing history persistence")
    source_quote_number: Optional[str] = Field(default=None, description="Quote number this price change came from, for pricing history persistence")
    processing_id: Optional[UUID] = Field(default=None, description="quote_processing run this price change came from, for pricing history persistence")
    line_item_id: Optional[UUID] = Field(default=None, description="quote_line_items row this price change came from, for pricing history persistence")


class AddPriceBreakResponseData(BaseModel):
    quantity: int
    price: float
    price_date: datetime
    effective_date: datetime
    inactive_date: Optional[datetime] = None


# --- API 3: Update Price Break ---
class UpdatePriceBreakRequest(BaseModel):
    quantity: int = Field(
        ..., gt=0,
        description="Quantity of the price break tier to update - identifies the target tier via business context, not a database ID",
    )
    price: float = Field(..., gt=0, description="Updated unit price")
    effective_date: datetime = Field(..., description="Updated effective date")
    inactive_date: Optional[datetime] = Field(default=None, description="Date the price break becomes inactive, if provided")
    # Optional business-key context for pricing_history persistence only - see AddPriceBreakRequest.
    evco_part_number: Optional[str] = Field(default=None, description="EVCO Part Number, for pricing history persistence")
    customer_number: Optional[str] = Field(default=None, description="Customer Number, for pricing history persistence")
    manufacturing_bom_number: Optional[str] = Field(default=None, description="Manufacturing/BOM Number, for pricing history persistence")
    currency: Optional[str] = Field(default="USD", description="Currency, for pricing history persistence")
    source_quote_number: Optional[str] = Field(default=None, description="Quote number this price change came from, for pricing history persistence")
    processing_id: Optional[UUID] = Field(default=None, description="quote_processing run this price change came from, for pricing history persistence")
    line_item_id: Optional[UUID] = Field(default=None, description="quote_line_items row this price change came from, for pricing history persistence")


class UpdatePriceBreakResponseData(BaseModel):
    quantity: int
    price: float
    effective_date: datetime
    inactive_date: Optional[datetime] = None
