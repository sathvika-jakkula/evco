from typing import Any, Dict, Optional
from app.core.schemas import ErrorCategory


class APIException(Exception):
    """Base class for all API exceptions formatted into standard error response."""

    def __init__(
        self,
        message: str = "Unable to process request",
        code: str = "API_ERROR",
        category: ErrorCategory = ErrorCategory.BUSINESS,
        status_code: int = 400,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


class BusinessException(APIException):
    """Raised for business logic or domain validation failures."""

    def __init__(
        self,
        message: str = "Unable to process request due to business rules",
        code: str = "BUSINESS_RULE_VIOLATION",
        status_code: int = 400,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            category=ErrorCategory.BUSINESS,
            status_code=status_code,
            retryable=retryable,
            details=details,
        )


class TechnicalException(APIException):
    """Raised for technical or infrastructure failures."""

    def __init__(
        self,
        message: str = "A technical error occurred",
        code: str = "TECHNICAL_ERROR",
        status_code: int = 500,
        retryable: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            category=ErrorCategory.TECHNICAL,
            status_code=status_code,
            retryable=retryable,
            details=details,
        )


class SecurityException(APIException):
    """Raised for authentication or authorization failures."""

    def __init__(
        self,
        message: str = "Security validation failed",
        code: str = "SECURITY_VIOLATION",
        status_code: int = 401,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            category=ErrorCategory.SECURITY,
            status_code=status_code,
            retryable=retryable,
            details=details,
        )
