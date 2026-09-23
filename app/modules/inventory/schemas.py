from typing import Generic, List, Optional, TypeVar
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

T = TypeVar("T")


class StandardInventoryResponse(BaseModel, Generic[T]):
    statusCode: int = Field(..., description="HTTP Status Code")
    message: str = Field(..., description="Response Message")
    data: Optional[T] = Field(None, description="Response Data Payload")


# --- Shared AKA shapes -------------------------------------------------

class AkaHeader(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="Item #", validation_alias=AliasChoices("evco_part_number", "Item #"))
    rev: str = Field(..., alias="Rev", validation_alias=AliasChoices("rev", "Rev"))
    description: str = Field(..., alias="Description", validation_alias=AliasChoices("part_description", "Description"))


class AkaDetail(BaseModel):
    """One customer/manufacturing-number AKA mapping for an item. Identity for
    lookup/update purposes is (item_number [from the enclosing Header],
    customer_number, manufacturing_bom_number) - akaItem# (the customer's own
    part number) is a mutable attribute of that mapping, not part of its key."""
    model_config = ConfigDict(populate_by_name=True)

    aka_item_number: str = Field(default="", alias="akaItem#", validation_alias=AliasChoices("customer_part_number", "akaItem#"))
    aka_description: str = Field(default="", alias="akaDescription", validation_alias=AliasChoices("part_description", "akaDescription"))
    rev: str = Field(default="", alias="rev")
    customer_number: str = Field(..., alias="customer#", validation_alias=AliasChoices("customer_number", "customer#"))
    currency: str = Field(default="USD", alias="currency")
    customer_name: str = Field(default="", alias="customername", validation_alias=AliasChoices("customer_name", "customername"))
    manufacturing_bom_number: str = Field(..., alias="mfg#", validation_alias=AliasChoices("manufacturing_bom_number", "mfg#"))
    ship_to_attn: str = Field(default="", alias="shipToAttn", validation_alias=AliasChoices("ship_to_attn", "shipToAttn"))
    minimum_selling_qty: int = Field(default=0, alias="minimumSellingQty", validation_alias=AliasChoices("moq", "minimumSellingQty"))
    selling_multiples_of: int = Field(default=0, alias="sellingMultiplesOf", validation_alias=AliasChoices("box_quantity", "sellingMultiplesOf"))
    so_item_number: str = Field(default="", alias="soItemNumber", validation_alias=AliasChoices("mold_number", "soItemNumber"), description="Mold number, called soItemNumber on the customer/IQMS side")


class AkaSearchData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    header: AkaHeader = Field(..., alias="Header", validation_alias=AliasChoices("header", "Header"))
    aka_details: List[AkaDetail] = Field(..., alias="akaDetails", validation_alias=AliasChoices("aka_details", "akaDetails"))


# --- API 1: Get AKA -------------------------------------------------------

class GetAkaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="evco_part_number", validation_alias=AliasChoices("evco_part_number", "Item #"))
    customer_number: str = Field(..., alias="customer_number", validation_alias=AliasChoices("customer_number", "customer#"))
    manufacturing_bom_number: str = Field(..., alias="manufacturing_bom_number", validation_alias=AliasChoices("manufacturing_bom_number", "mfg#"))
    line_item_id: Optional[UUID] = Field(
        default=None, description="quote_line_items row that triggered this lookup, for traceability"
    )


# --- API 2: Create AKA -----------------------------------------------------

class CreateAkaDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    aka_item_number: str = Field(..., alias="customer_part_number", validation_alias=AliasChoices("customer_part_number", "akaItem#"))
    aka_description: str = Field(default="", alias="part_description", validation_alias=AliasChoices("part_description", "akaDescription"))
    rev: str = Field(default="", alias="rev")
    customer_number: str = Field(..., alias="customer_number", validation_alias=AliasChoices("customer_number", "customer#"))
    currency: str = Field(default="USD", alias="currency")
    customer_name: str = Field(default="", alias="customer_name", validation_alias=AliasChoices("customer_name", "customername"))
    manufacturing_bom_number: str = Field(..., alias="manufacturing_bom_number", validation_alias=AliasChoices("manufacturing_bom_number", "mfg#"))
    ship_to_attn: str = Field(default="", alias="ship_to_attn", validation_alias=AliasChoices("ship_to_attn", "shipToAttn"))
    minimum_selling_qty: int = Field(default=0, alias="moq", validation_alias=AliasChoices("moq", "minimumSellingQty"))
    selling_multiples_of: int = Field(default=0, alias="box_quantity", validation_alias=AliasChoices("box_quantity", "sellingMultiplesOf"))
    so_item_number: str = Field(default="", alias="mold_number", validation_alias=AliasChoices("mold_number", "soItemNumber"), description="Mold number, called soItemNumber on the customer/IQMS side")


class CreateAkaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="evco_part_number", validation_alias=AliasChoices("evco_part_number", "Item #"))
    create_aka_details: CreateAkaDetails = Field(..., alias="create_aka_details", validation_alias=AliasChoices("create_aka_details", "createAkaDetails"))
    line_item_id: Optional[UUID] = Field(
        default=None, description="quote_line_items row that triggered this creation, for traceability"
    )


# --- API 3: Update AKA -----------------------------------------------------

class UpdateAkaDetails(BaseModel):
    """Mutable fields only - customer#/mfg# identify the target record (see
    UpdateAkaRequest) and are not themselves updatable through this API."""
    model_config = ConfigDict(populate_by_name=True)

    aka_item_number: Optional[str] = Field(default=None, alias="customer_part_number", validation_alias=AliasChoices("customer_part_number", "akaItem#"))
    aka_description: Optional[str] = Field(default=None, alias="part_description", validation_alias=AliasChoices("part_description", "akaDescription"))
    rev: Optional[str] = Field(default=None, alias="rev")
    currency: Optional[str] = Field(default=None, alias="currency")
    ship_to_attn: Optional[str] = Field(default=None, alias="ship_to_attn", validation_alias=AliasChoices("ship_to_attn", "shipToAttn"))
    minimum_selling_qty: Optional[int] = Field(default=None, alias="moq", validation_alias=AliasChoices("moq", "minimumSellingQty"))
    selling_multiples_of: Optional[int] = Field(default=None, alias="box_quantity", validation_alias=AliasChoices("box_quantity", "sellingMultiplesOf"))
    so_item_number: Optional[str] = Field(default=None, alias="mold_number", validation_alias=AliasChoices("mold_number", "soItemNumber"), description="Mold number, called soItemNumber on the customer/IQMS side")


class UpdateAkaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_number: str = Field(..., alias="evco_part_number", validation_alias=AliasChoices("evco_part_number", "Item #"))
    customer_number: str = Field(..., alias="customer_number", validation_alias=AliasChoices("customer_number", "customer#"))
    manufacturing_bom_number: str = Field(..., alias="manufacturing_bom_number", validation_alias=AliasChoices("manufacturing_bom_number", "mfg#"))
    update_aka_details: UpdateAkaDetails = Field(..., alias="update_aka_details", validation_alias=AliasChoices("update_aka_details", "updateAkaDetails"))
    line_item_id: Optional[UUID] = Field(
        default=None, description="quote_line_items row that triggered this update, for traceability"
    )


# HTTP response views use extraction terminology. The original models above
# retain their serialization aliases for stored AKA audit snapshots.
class AkaHeaderResponse(AkaHeader):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    item_number: str = Field(alias="evco_part_number")
    rev: str
    description: str = Field(alias="part_description")


class AkaDetailResponse(AkaDetail):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    aka_item_number: str = Field(default="", alias="customer_part_number")
    aka_description: str = Field(default="", alias="part_description")
    customer_number: str
    customer_name: str = ""
    manufacturing_bom_number: str
    ship_to_attn: str = ""
    minimum_selling_qty: int = Field(default=0, alias="moq")
    selling_multiples_of: int = Field(default=0, alias="box_quantity")
    so_item_number: str = Field(default="", alias="mold_number")


class AkaSearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    header: AkaHeaderResponse
    aka_details: List[AkaDetailResponse]
