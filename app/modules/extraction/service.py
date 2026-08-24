import asyncio
import json
import logging
import unicodedata
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import HTTPException, status

from app.core.exceptions import BusinessException
from app.database.quote_processing_repository import QuoteProcessingRepository
from app.database.quote_scan_repository import QuoteScanRepository
from app.core.schemas import ErrorCategory, error_response, success_response
from app.modules.extraction.parser import PDFExtractor, QuoteExtractionResponse

logger = logging.getLogger(__name__)


def _attach_line_item_ids(quote_data: Dict[str, Any], processing_id: UUID, line_item_ids: List[UUID]) -> Dict[str, Any]:
    """Embed processing_id and per-part/tier line_item_id onto an extracted quote dict, in place."""
    quote_data["processing_id"] = str(processing_id)
    line_item_position = 0
    for part in quote_data.get("parts") or []:
        pricing_tiers = part.get("pricing_tiers") or [None]
        part_ids = line_item_ids[line_item_position:line_item_position + len(pricing_tiers)]
        line_item_position += len(pricing_tiers)
        if part_ids:
            # The first ID is convenient for a single-tier part; tier IDs
            # below preserve the exact database row for multi-tier pricing.
            part["line_item_id"] = str(part_ids[0])
        for tier, line_item_id in zip(part.get("pricing_tiers") or [], part_ids):
            tier["line_item_id"] = str(line_item_id)
    return quote_data


class ExtractionService:
    def __init__(
        self,
        processing_repository: QuoteProcessingRepository | None = None,
        scan_repository: QuoteScanRepository | None = None,
    ) -> None:
        self.extractor = PDFExtractor()
        self.processing_repository = processing_repository or QuoteProcessingRepository()
        self.scan_repository = scan_repository or QuoteScanRepository()

    def extract_quote(self, pdf_path: Path) -> QuoteExtractionResponse:
        return self.extractor.extract_quote(pdf_path)

    def resolve_scan_file_id(self, scan_id: Optional[UUID], file_name: str) -> Optional[UUID]:
        if scan_id is None:
            return None
        scan_file_id = self.scan_repository.resolve_scan_file_id(scan_id, file_name)
        if scan_file_id is None:
            raise BusinessException(
                message="No scanned file matches the given scan_id and file_name",
                code="SCAN_FILE_NOT_FOUND",
                status_code=404,
                details={"scan_id": str(scan_id), "file_name": file_name},
            )
        return scan_file_id

    def get_processing_result(self, processing_id: UUID) -> Dict[str, Any]:
        row = self.processing_repository.get_processing_row(processing_id)
        if row is None:
            raise BusinessException(
                message="No processing record found for the given processing_id",
                code="PROCESSING_ID_NOT_FOUND",
                status_code=404,
                details={"processing_id": str(processing_id)},
            )
        raw_extracted_json = row["raw_extracted_json"]
        if raw_extracted_json is None:
            raise BusinessException(
                message="Extraction for this processing_id has not completed yet",
                code="PROCESSING_NOT_COMPLETE",
                status_code=409,
                details={"processing_id": str(processing_id), "processing_status": row["processing_status"]},
            )
        quote_data = json.loads(raw_extracted_json) if isinstance(raw_extracted_json, str) else raw_extracted_json
        if row["processing_status"] == "FAILED":
            # mark_processing_failed stores {"error": reason}, not a full quote
            # schema - there are no line items to attach in that case.
            return quote_data
        line_item_ids = self.processing_repository.get_line_item_ids(processing_id)
        return _attach_line_item_ids(quote_data, processing_id, line_item_ids)

    async def resolve_pdf_file_path(self, file_name: str, folder_path: str) -> Path:
        def normalize_filename(name: str) -> str:
            normalized = unicodedata.normalize("NFKC", name)
            dashes = ["‐", "‑", "‒", "–", "—", "−"]
            for dash in dashes:
                normalized = normalized.replace(dash, "-")
            return normalized.strip()

        if "/" in file_name or "\\" in file_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_name must not include directory components.",
            )

        requested_name = normalize_filename(file_name)
        source_folder = Path(folder_path).expanduser().resolve()
        if not source_folder.exists() or not source_folder.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Folder '{folder_path}' does not exist or is not accessible.",
            )
        file_path = source_folder / requested_name

        if not file_path.exists() or not file_path.is_file():
            found_path = None
            if source_folder.is_dir():
                target_norm = requested_name.lower()
                for candidate in source_folder.iterdir():
                    if candidate.is_file() and normalize_filename(candidate.name).lower() == target_norm:
                        found_path = candidate
                        break
            if found_path:
                file_path = found_path
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"File '{file_name}' not found in folder '{source_folder}'.",
                )

        if file_path.suffix.lower() != ".pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PDF files are supported.",
            )

        return file_path

    async def run_callback_extraction(self, callback_url: str, file_path: Path, processing_id: UUID) -> None:
        try:
            started_at = time.perf_counter()
            quote_data = await asyncio.to_thread(self.extract_quote, file_path)
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            line_item_ids = await asyncio.to_thread(
                self.processing_repository.save_extraction_result,
                processing_id,
                file_path,
                quote_data,
                duration_ms,
            )
            callback_data = _attach_line_item_ids(quote_data.model_dump(mode="json"), processing_id, line_item_ids)

            # quote_data.is_exception (set by parser.py's BRM-001/BRM-002
            # required-field validation, e.g. an inactive quote or a
            # missing required table column) means this quote was
            # deliberately EXCLUDED from processing, not that extraction
            # itself failed. The HTTP call still succeeds and parts is
            # empty by design - but the message/log must say so plainly,
            # since callback_data.is_exception + exception_reason are the
            # only signal the receiving side has that this quote needs
            # manual review rather than being fully processed.
            if quote_data.is_exception:
                message = f"Quote excluded from processing [{', '.join(quote_data.exception_codes)}]: {quote_data.exception_reason}"
                logger.warning(
                    "Quote %s excluded from processing (processing_id=%s, codes=%s): %s",
                    file_path.name, processing_id, quote_data.exception_codes, quote_data.exception_reason,
                )
            else:
                message = "Quote extraction completed successfully"
                logger.info(f"Extracted quote: {callback_data}")

            payload = success_response(
                data=callback_data,
                message=message,
                status_code=200,
            ).model_dump(mode="json")
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(callback_url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            logger.error("Async callback extraction failed for %s: %s", file_path, exc)
            try:
                await asyncio.to_thread(self.processing_repository.mark_processing_failed, processing_id, str(exc))
            except Exception as persistence_error:
                logger.error("Unable to mark processing run %s as failed: %s", processing_id, persistence_error)
            try:
                err_payload = error_response(
                    message=f"Extraction failed for {file_path.name}: {exc}",
                    code="EXTRACTION_FAILED",
                    category=ErrorCategory.TECHNICAL,
                    status_code=500,
                    retryable=True,
                    details={"file_name": file_path.name},
                ).model_dump(mode="json")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.post(callback_url, json=err_payload)
            except Exception as callback_error:
                logger.error("Failed to deliver callback error for %s: %s", file_path, callback_error)
