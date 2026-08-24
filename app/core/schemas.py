from enum import Enum
from typing import Any, Dict, Generic, Optional, TypeVar
from pydantic import BaseModel, Field


class ErrorCategory(str, Enum):
    BUSINESS = "BUSINESS"
    TECHNICAL = "TECHNICAL"
    SECURITY = "SECURITY"


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error code")
    category: ErrorCategory = Field(..., description="Error category: BUSINESS | TECHNICAL | SECURITY")
    retryable: bool = Field(default=False, description="Indicates if caller can retry the request")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional error context or metadata")


T = TypeVar("T")


class StandardResponse(BaseModel, Generic[T]):
    statusCode: int = Field(default=200, description="HTTP status code")
    message: str = Field(default="Operation completed successfully", description="Status message description")
    data: Optional[T] = Field(default=None, description="Response payload data")
    error: Optional[ErrorDetail] = Field(default=None, description="Error detail payload if request failed")


def success_response(
    data: Any,
    message: str = "Operation completed successfully",
    status_code: int = 200,
) -> StandardResponse[Any]:
    return StandardResponse(
        statusCode=status_code,
        message=message,
        data=data,
        error=None,
    )


def error_response(
    message: str = "Unable to process request",
    code: str = "BAD_REQUEST",
    category: ErrorCategory = ErrorCategory.BUSINESS,
    status_code: int = 400,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> StandardResponse[None]:
    return StandardResponse(
        statusCode=status_code,
        message=message,
        data=None,
        error=ErrorDetail(
            code=code,
            category=category,
            retryable=retryable,
            details=details or {},
        ),
    )
