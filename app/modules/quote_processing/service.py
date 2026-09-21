"""Quote processing pipeline result/exception persistence (T17-T20) and the
final move-to-folder step (T22). T18-T20 are not yet implemented."""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.exceptions import BusinessException
from app.database.api_audit_log_repository import ApiAuditLogRepository
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.database.quote_scan_repository import QuoteScanRepository
from app.modules.quote_processing.schemas import (
    ExceptionRecordData,
    MoveQuoteFileRequest,
    MoveQuoteFileResponseData,
    RecordExceptionRequest,
)

logger = logging.getLogger(__name__)


class QuoteProcessingResultService:
    def __init__(
        self, processing_repository: QuoteProcessingRepository | None = None,
        scan_repository: QuoteScanRepository | None = None,
        audit_log_repository: ApiAuditLogRepository | None = None,
    ) -> None:
        self.processing_repository = processing_repository or QuoteProcessingRepository()
        self.scan_repository = scan_repository or QuoteScanRepository()
        self.audit_log_repository = audit_log_repository or ApiAuditLogRepository()

    def record_exception(self, req: RecordExceptionRequest) -> ExceptionRecordData:
        created_at = datetime.now(timezone.utc)
        exception_id = self.processing_repository.record_exception(
            processing_id=req.processing_id,
            agent_name=req.agent_name,
            tool_name=req.tool_name,
            exception_code=req.exception_code,
            exception_message=req.exception_message,
            line_item_id=req.line_item_id,
            retry_attempt=req.retry_attempt,
            max_retry_count=req.max_retry_count,
            is_retryable=req.is_retryable,
            resolved=req.resolved,
            resolved_by=req.resolved_by,
            resolved_time=req.resolved_time,
        )
        return ExceptionRecordData(
            exception_id=exception_id,
            processing_id=req.processing_id,
            line_item_id=req.line_item_id,
            agent_name=req.agent_name,
            tool_name=req.tool_name,
            exception_code=req.exception_code,
            exception_message=req.exception_message,
            retry_attempt=req.retry_attempt,
            max_retry_count=req.max_retry_count,
            is_retryable=req.is_retryable,
            resolved=req.resolved,
            resolved_by=req.resolved_by,
            resolved_time=req.resolved_time,
            created_at=created_at,
        )

    def move_quote_file(self, req: MoveQuoteFileRequest) -> MoveQuoteFileResponseData:
        source_folder = Path(req.from_folder).expanduser().resolve()
        source_path = source_folder / req.filename
        if not source_path.exists() or not source_path.is_file():
            # A missing source file is a caller error (wrong filename/folder), not a
            # business exception worth recording - matches the existing 404 contract.
            raise BusinessException(
                message="Quote file not found in the given from_folder",
                code="QUOTE_FILE_NOT_FOUND",
                status_code=404,
                details={"filename": req.filename, "from_folder": str(source_folder)},
            )

        destination_dir = Path(req.to_folder).expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path = destination_dir / source_path.name

        started_at = datetime.now(timezone.utc)
        try:
            shutil.move(str(source_path), str(destination_path))
        except OSError as exc:
            duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            self._log_move_call("FAILED", duration_ms, req, str(exc))
            try:
                self.processing_repository.record_exception(
                    processing_id=None, agent_name="quote_processing", tool_name="move_quote_file",
                    exception_code="QUOTE_FILE_MOVE_FAILED", exception_message=str(exc),
                )
            except Exception:
                logger.exception("Failed to record QUOTE_FILE_MOVE_FAILED exception for %s", req.filename)
            return MoveQuoteFileResponseData(
                filename=source_path.name,
                outcome=req.outcome,
                source_path=str(source_path),
                destination_path=str(destination_path),
                moved=False,
            )

        duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        self._log_move_call("SUCCESS", duration_ms, req, None)
        try:
            self.scan_repository.mark_file_moved(req.filename)
        except Exception:
            logger.exception("Failed to mark quote_scan_files.file_status=MOVED for %s", req.filename)

        return MoveQuoteFileResponseData(
            filename=source_path.name,
            outcome=req.outcome,
            source_path=str(source_path),
            destination_path=str(destination_path),
            moved=True,
        )

    def _log_move_call(self, status: str, duration_ms: int, req: MoveQuoteFileRequest, error: str | None) -> None:
        try:
            self.audit_log_repository.log_call(
                endpoint="/quote-processing/move-quote-file", http_method="POST", status=status,
                duration_ms=duration_ms,
                request_payload=req.model_dump(mode="json"),
                response_payload={"error": error} if error else None,
            )
        except Exception:
            logger.exception("Failed to record api_audit_logs entry for move-quote-file (%s)", req.filename)


# Singleton instance, mirroring the other services in this codebase
quote_processing_result_service = QuoteProcessingResultService()
