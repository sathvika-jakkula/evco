"""Filesystem-backed quote scan service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.core.exceptions import BusinessException, TechnicalException
from app.database.quote_scan_repository import QuoteScanRepository


class MonitoringService:
    """Lists quote PDFs from a monitored incoming folder and records each scan."""

    def __init__(self, scan_repository: QuoteScanRepository | None = None) -> None:
        self.scan_repository = scan_repository or QuoteScanRepository()

    def scan_and_claim(self, folder_path: str, recursive: bool) -> tuple[UUID, datetime, list[str]]:
        scan_started_at = datetime.now(timezone.utc)
        incoming_folder = Path(folder_path).expanduser().resolve()
        if not incoming_folder.exists():
            raise BusinessException(
                message="The monitored folder does not exist",
                code="MONITORED_FOLDER_NOT_FOUND",
                status_code=404,
                details={"folder_path": str(incoming_folder)},
            )
        if not incoming_folder.is_dir():
            raise BusinessException(
                message="The monitored path must be a folder",
                code="MONITORED_PATH_NOT_DIRECTORY",
                status_code=400,
                details={"folder_path": str(incoming_folder)},
            )

        candidates = incoming_folder.rglob("*") if recursive else incoming_folder.glob("*")
        detected_files: list[str] = []
        try:
            for candidate in sorted(candidates, key=lambda item: str(item).casefold()):
                if not candidate.is_file() or candidate.suffix.casefold() != ".pdf":
                    continue
                relative_path = candidate.relative_to(incoming_folder)
                detected_files.append(relative_path.as_posix())
        except OSError as exc:
            raise TechnicalException(
                message="Unable to scan the monitored folder",
                code="FOLDER_SCAN_FAILED",
                details={"folder_path": str(incoming_folder)},
            ) from exc

        scan_id = uuid4()
        self.scan_repository.record_scan(scan_id, detected_files, scan_started_at)
        return scan_id, datetime.now(timezone.utc), detected_files
