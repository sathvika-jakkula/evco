from __future__ import annotations

"""PostgreSQL connection helper for quote-processing persistence."""

from app.core.config import settings
from app.core.exceptions import TechnicalException


class DatabaseConnection:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.DATABASE_URL

    def connect(self):
        if not self.database_url:
            raise TechnicalException(
                message="DATABASE_URL is not configured",
                code="DATABASE_NOT_CONFIGURED",
                details={"setting": "DATABASE_URL"},
            )
        try:
            import psycopg
            return psycopg.connect(self.database_url)
        except ImportError as exc:
            raise TechnicalException(
                message="PostgreSQL driver is not installed",
                code="DATABASE_DRIVER_UNAVAILABLE",
            ) from exc
        except Exception as exc:
            raise TechnicalException(
                message="Unable to connect to the quote-processing database",
                code="DATABASE_CONNECTION_FAILED",
            ) from exc
