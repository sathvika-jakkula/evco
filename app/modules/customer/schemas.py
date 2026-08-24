from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from app.core.schemas import StandardResponse


class MatchStatusEnum(str, Enum):
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


class CustomerResolveRequest(BaseModel):
    processing_id: Optional[UUID] = Field(
        default=None,
        description="Optional quote_processing identifier. When supplied, the resolution is persisted.",
    )
    part_number: Optional[str] = Field(default=None, description="Part identifier under evaluation")
    quote_number: Optional[str] = Field(default=None, description="Quote identifier under evaluation")
    customer_name: str = Field(..., description="Customer name extracted from the quote")
    customer_number: Optional[str] = Field(default=None, description="Customer number if present on the quote")
    aka_customer_number: Optional[str] = Field(
        default=None,
        description="Customer/sold-to number tied to the AKA record; used to detect EX-005 mismatches.",
    )


class CustomerCandidateSchema(BaseModel):
    ar_custo_id: int = Field(..., description="DELMIAWorks / IQMS customer record ID")
    customer_name: str = Field(..., description="Matching customer name")
    customer_number: Optional[str] = Field(default=None, description="Matching customer number")
    address: Optional[str] = Field(default=None, description="Customer address")


class CustomerResolveData(BaseModel):
    match_status: MatchStatusEnum = Field(..., description="Resolution status: UNIQUE | AMBIGUOUS | NOT_FOUND")
    candidates: List[CustomerCandidateSchema] = Field(default_factory=list, description="Matching DELMIAWorks customer records")
    customer_resolution_id: Optional[UUID] = Field(
        default=None,
        description="Primary key of the customer_resolution_results row created for a unique resolution",
    )
    exception_codes: List[str] = Field(default_factory=list, description="Customer-resolution exception codes")
    exception_ids: List[UUID] = Field(default_factory=list, description="Created exception_logs primary keys")


class CustomerResolveResponse(StandardResponse[CustomerResolveData]):
    statusCode: int = Field(default=200, description="HTTP status code")
    message: str = Field(default="Customer resolution completed successfully", description="Status description message")
    data: Optional[CustomerResolveData] = Field(default=None, description="Resolution payload containing match status and candidates")
