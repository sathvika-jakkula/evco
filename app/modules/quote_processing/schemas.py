from datetime import datetime
from typing import List
from enum import Enum
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


# --- T17: Record Exceptions ---
class RecordExceptionRequest(BaseModel):
    processing_id: UUID = Field(..., description="processing_id (batch/quote run) this exception belongs to")
    line_item_id: Optional[UUID] = Field(
        default=None, description="Line item this exception applies to - omit for a batch/quote-level exception"
    )
    agent_name: Optional[str] = Field(default=None, description="Name of the agent that raised this exception")
    tool_name: Optional[str] = Field(default=None, description="Name of the tool/step that raised this exception")
    exception_code: str = Field(..., description="Exception or rule code")
    exception_message: str = Field(..., description="Human-readable exception detail")
    retry_attempt: int = Field(default=0, ge=0)
    max_retry_count: int = Field(default=0, ge=0)
    is_retryable: bool = Field(default=False)
    resolved: bool = Field(default=False)
    resolved_by: Optional[str] = Field(default=None)
    resolved_time: Optional[datetime] = Field(default=None)


class ExceptionRecordData(BaseModel):
    exception_id: UUID
    processing_id: UUID
    line_item_id: Optional[UUID] = None
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    exception_code: str
    exception_message: str
    retry_attempt: int
    max_retry_count: int
    is_retryable: bool
    resolved: bool
    resolved_by: Optional[str] = None
    resolved_time: Optional[datetime] = None
    created_at: datetime


# --- T22: Move to Exceptions/Processed folder ---
class QuoteOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class MoveQuoteFileRequest(BaseModel):
    filename: str = Field(..., description="Filename (within from_folder) of the quote file to move")
    from_folder: str = Field(..., min_length=1, description="Source folder currently containing the file")
    to_folder: str = Field(..., min_length=1, description="Destination folder to move the file into")
    outcome: QuoteOutcome = Field(..., description="Outcome this move represents, for the audit trail")


class MoveQuoteFileResponseData(BaseModel):
    filename: str
    outcome: QuoteOutcome
    source_path: str
    destination_path: str
    moved: bool


# --- Record review rows (one call per line and review area) ---
class ReviewArea(str, Enum):
    AKA = "AKA"
    PRICING = "PRICING"
    SALES_ORDER = "SALES_ORDER"


class ReviewRowStatus(str, Enum):
    UPDATE_REQUIRED = "UPDATE_REQUIRED"
    NO_ACTION = "NO_ACTION"
    NOT_VERIFIABLE = "NOT_VERIFIABLE"


class ReviewRowInput(BaseModel):
    rule_code: str = Field(..., min_length=1)
    check: str = Field(..., min_length=1)
    status: ReviewRowStatus
    message: str = Field(..., min_length=1)
    field: Optional[str] = None
    record_ref: Optional[str] = None
    sales_order_id: Optional[str] = None
    sales_order_detail_id: Optional[str] = None
    release_id: Optional[str] = None
    quote_value: Optional[str] = None
    system_value: Optional[str] = None
    proposed_value: Optional[str] = None
    exception_id: Optional[UUID] = None


class RecordReviewRowsRequest(BaseModel):
    processing_id: UUID
    line_item_id: UUID
    review_area: ReviewArea
    quote_number: str = Field(..., min_length=1)
    line_number: Optional[str] = None
    evco_part_number: Optional[str] = None
    mold_number: Optional[str] = None
    manufacturing_bom_number: Optional[str] = None
    customer_number: Optional[str] = None
    rows: List[ReviewRowInput] = Field(default_factory=list, max_length=200)


class RecordReviewRowsData(BaseModel):
    rows_stored: int
    rows_replaced: int
    review_row_ids: List[UUID]
