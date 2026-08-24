from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

# No IDs (arinvt_id / arCustoId / priceBreakId) are accepted anywhere in the
# Price Break API - the RPA operates on the customer/item context already
# open in IQMS and identifies the relevant price break itself.


# --- API 1: Get Price Breaks ---
class GetPriceBreaksRequest(BaseModel):
    evco_part_number: str = Field(..., description="EVCO Part Number to retrieve price breaks for")
    customer_number: str = Field(..., description="Customer Number to retrieve price breaks for")
    manufacturing_bom_number: str = Field(..., description="Manufacturing/BOM Number to retrieve price breaks for")


class PriceBreakData(BaseModel):
    unit_price: float
    quantity: int
    comment: str


# --- API 2: Add Price Break ---
class AddPriceBreakRequest(BaseModel):
    quantity: int = Field(..., gt=0, description="Quantity threshold for the new price break tier")
    price: float = Field(..., gt=0, description="Unit price for the new price break tier")
    effective_date: datetime = Field(..., description="Date the price break becomes effective")


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


class UpdatePriceBreakResponseData(BaseModel):
    quantity: int
    price: float
    effective_date: datetime
    inactive_date: Optional[datetime] = None
