from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    folder_path: str = Field(..., min_length=1, description="Absolute or relative incoming-folder path")
    recursive: bool = Field(default=False, description="Include subfolders in the scan")


class ScanResponseData(BaseModel):
    scan_id: UUID = Field(..., description="Unique scan event identifier")
    scan_timestamp: datetime = Field(..., description="UTC time at which the scan was executed")
    files_detected: list[str] = Field(default_factory=list, description="PDFs claimed by this scan")
    file_count: int = Field(..., ge=0, description="Number of PDFs claimed by this scan")
