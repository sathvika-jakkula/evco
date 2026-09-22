from typing import Generic, List, Optional, TypeVar
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class StandardInventoryResponse(BaseModel, Generic[T]):
    statusCode: int = Field(..., description="HTTP Status Code")
    message: str = Field(..., description="Response Message")
    data: Optional[T] = Field(None, description="Response Data Payload")


# --- Shared AKA shapes -------------------------------------------------

class AkaHeader(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="Item #")
    rev: str = Field(..., alias="Rev")
    description: str = Field(..., alias="Description")


class AkaDetail(BaseModel):
    """One customer/manufacturing-number AKA mapping for an item. Identity for
    lookup/update purposes is (item_number [from the enclosing Header],
    customer_number, manufacturing_bom_number) - akaItem# (the customer's own
    part number) is a mutable attribute of that mapping, not part of its key."""
    model_config = ConfigDict(populate_by_name=True)

    aka_item_number: str = Field(default="", alias="akaItem#")
    aka_description: str = Field(default="", alias="akaDescription")
    rev: str = Field(default="", alias="rev")
    customer_number: str = Field(..., alias="customer#")
    currency: str = Field(default="USD", alias="currency")
    customer_name: str = Field(default="", alias="customername")
    manufacturing_bom_number: str = Field(..., alias="mfg#")
    ship_to_attn: str = Field(default="", alias="shipToAttn")
    minimum_selling_qty: int = Field(default=0, alias="minimumSellingQty")
    selling_multiples_of: int = Field(default=0, alias="sellingMultiplesOf")
    so_item_number: str = Field(default="", alias="soItemNumber", description="Mold number, called soItemNumber on the customer/IQMS side")


class AkaSearchData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    header: AkaHeader = Field(..., alias="Header")
    aka_details: List[AkaDetail] = Field(..., alias="akaDetails")


# --- API 1: Get AKA -------------------------------------------------------

class GetAkaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="Item #")
    customer_number: str = Field(..., alias="customer#")
    manufacturing_bom_number: str = Field(..., alias="mfg#")
    line_item_id: Optional[UUID] = Field(
        default=None, description="quote_line_items row that triggered this lookup, for traceability"
    )


# --- API 2: Create AKA -----------------------------------------------------

class CreateAkaDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    aka_item_number: str = Field(..., alias="akaItem#")
    aka_description: str = Field(default="", alias="akaDescription")
    rev: str = Field(default="", alias="rev")
    customer_number: str = Field(..., alias="customer#")
    currency: str = Field(default="USD", alias="currency")
    customer_name: str = Field(default="", alias="customername")
    manufacturing_bom_number: str = Field(..., alias="mfg#")
    ship_to_attn: str = Field(default="", alias="shipToAttn")
    minimum_selling_qty: int = Field(default=0, alias="minimumSellingQty")
    selling_multiples_of: int = Field(default=0, alias="sellingMultiplesOf")
    so_item_number: str = Field(default="", alias="soItemNumber", description="Mold number, called soItemNumber on the customer/IQMS side")


class CreateAkaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="Item #")
    create_aka_details: CreateAkaDetails = Field(..., alias="createAkaDetails")
    line_item_id: Optional[UUID] = Field(
        default=None, description="quote_line_items row that triggered this creation, for traceability"
    )


# --- API 3: Update AKA -----------------------------------------------------

class UpdateAkaDetails(BaseModel):
    """Mutable fields only - customer#/mfg# identify the target record (see
    UpdateAkaRequest) and are not themselves updatable through this API."""
    model_config = ConfigDict(populate_by_name=True)

    aka_item_number: Optional[str] = Field(default=None, alias="akaItem#")
    aka_description: Optional[str] = Field(default=None, alias="akaDescription")
    rev: Optional[str] = Field(default=None, alias="rev")
    currency: Optional[str] = Field(default=None, alias="currency")
    ship_to_attn: Optional[str] = Field(default=None, alias="shipToAttn")
    minimum_selling_qty: Optional[int] = Field(default=None, alias="minimumSellingQty")
    selling_multiples_of: Optional[int] = Field(default=None, alias="sellingMultiplesOf")
    so_item_number: Optional[str] = Field(default=None, alias="soItemNumber", description="Mold number, called soItemNumber on the customer/IQMS side")


class UpdateAkaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="Item #")
    customer_number: str = Field(..., alias="customer#")
    manufacturing_bom_number: str = Field(..., alias="mfg#")
    update_aka_details: UpdateAkaDetails = Field(..., alias="updateAkaDetails")
    line_item_id: Optional[UUID] = Field(
        default=None, description="quote_line_items row that triggered this update, for traceability"
    )
