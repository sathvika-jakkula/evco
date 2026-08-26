"""Quote processing pipeline result/exception persistence (T17-T20) and the
final move-to-folder step (T22). T18-T20 are not yet implemented."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.exceptions import BusinessException
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.modules.quote_processing.schemas import (
    ExceptionRecordData,
    MoveQuoteFileRequest,
    MoveQuoteFileResponseData,
    RecordExceptionRequest,
)


class QuoteProcessingResultService:
    def __init__(self, processing_repository: QuoteProcessingRepository | None = None) -> None:
        self.processing_repository = processing_repository or QuoteProcessingRepository()

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
            raise BusinessException(
                message="Quote file not found in the given from_folder",
                code="QUOTE_FILE_NOT_FOUND",
                status_code=404,
                details={"filename": req.filename, "from_folder": str(source_folder)},
            )

        destination_dir = Path(req.to_folder).expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path = destination_dir / source_path.name

        shutil.move(str(source_path), str(destination_path))

        return MoveQuoteFileResponseData(
            filename=source_path.name,
            outcome=req.outcome,
            source_path=str(source_path),
            destination_path=str(destination_path),
            moved=True,
        )


# Singleton instance, mirroring the other services in this codebase
quote_processing_result_service = QuoteProcessingResultService()
