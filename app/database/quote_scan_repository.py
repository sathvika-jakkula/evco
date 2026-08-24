"""Persistence operations for the quote_scans / quote_scan_files tables."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class QuoteScanRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def record_scan(self, scan_id: UUID, filenames: list[str], scan_started_at: datetime) -> None:
        """Insert one quote_scans row plus one quote_scan_files row per filename, in a single transaction."""
        now = datetime.now(timezone.utc)
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO quote_scans (scan_id, scan_status, total_files_found, successful_files,
                   failed_files, scan_started_at, scan_completed_at, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (scan_id, "COMPLETED", len(filenames), 0, 0, scan_started_at, now, now),
            )
            for filename in filenames:
                cursor.execute(
                    """INSERT INTO quote_scan_files (scan_file_id, scan_id, filename, file_status, created_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (uuid4(), scan_id, filename, "SCANNED", now),
                )

    def resolve_scan_file_id(self, scan_id: UUID, filename: str) -> UUID | None:
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT scan_file_id FROM quote_scan_files WHERE scan_id = %s AND filename = %s",
                (scan_id, filename),
            )
            row = cursor.fetchone()
            return row[0] if row else None
