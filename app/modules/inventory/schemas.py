from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class StandardInventoryResponse(BaseModel, Generic[T]):
    statusCode: int = Field(..., description="HTTP Status Code")
    message: str = Field(..., description="Response Message")
    data: Optional[T] = Field(None, description="Response Data Payload")


# --- API 1: Search Inventory Part ---
class SearchPartRequest(BaseModel):
    evco_part_number: str = Field(..., description="EVCO Part Number to search")


class InventoryPartData(BaseModel):
    evco_part_number: str
    item_number: str
    description: str
    inventory_class: str


# --- API 2: Get BOM Candidates ---
class GetBomCandidatesRequest(BaseModel):
    evco_part_number: str = Field(..., description="EVCO Part Number to get BOM candidates for")


class BomCandidateData(BaseModel):
    manufacturing_bom_number: str
    bom_description: str
    item_number: str


# --- API 3: Get AKA ---
class GetAkaRequest(BaseModel):
    customer_number: str = Field(..., description="Customer Number")
    customer_part_number: str = Field(..., description="Customer Part Number")
    item_number: str = Field(..., description="EVCO Item Number")


class AkaRecordData(BaseModel):
    customer_number: str
    customer_part_number: str
    item_number: str
    aka_description: str
    item_description: str
    uom: str
    currency: str
    manufacturing_bom_number: str
    moq: int
    selling_multiples_of: int


# --- API 4: Create AKA ---
class CreateAkaRequest(BaseModel):
    customer_number: str
    aka_item_number: str
    aka_description: str
    item_number: str
    item_description: str
    uom: str
    currency: str
    manufacturing_bom_number: str
    moq: int
    selling_multiples_of: int


class CreateAkaResponseData(BaseModel):
    status: str = Field("CREATED")
    customer_number: str
    aka_item_number: str
    item_number: str
    manufacturing_bom_number: str


# --- API 5: Update AKA ---
class UpdateAkaRequest(BaseModel):
    customer_number: str
    customer_part_number: str
    item_number: str
    aka_description: Optional[str] = None
    item_description: Optional[str] = None
    currency: Optional[str] = None
    manufacturing_bom_number: Optional[str] = None
    moq: Optional[int] = None
    selling_multiples_of: Optional[int] = None


class AkaStateBeforeAfter(BaseModel):
    aka_description: str
    item_description: str
    currency: str
    manufacturing_bom_number: str
    moq: int
    selling_multiples_of: int


class UpdateAkaResponseData(BaseModel):
    status: str = Field("UPDATED")
    customer_number: str
    customer_part_number: str
    item_number: str
    before: AkaStateBeforeAfter
    after: AkaStateBeforeAfter
