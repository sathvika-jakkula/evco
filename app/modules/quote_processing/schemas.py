from datetime import datetime
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
    agent_name: str = Field(..., description="Name of the agent that raised this exception")
    tool_name: str = Field(..., description="Name of the tool/step that raised this exception")
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
    agent_name: str
    tool_name: str
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
