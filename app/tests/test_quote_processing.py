from datetime import date
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import validate_access_token
import app.modules.quote_processing.router as quote_processing_router
from app.modules.quote_processing.schemas import MoveQuoteFileRequest
from app.modules.quote_processing.service import QuoteProcessingResultService

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_auth_dependency():
    app.dependency_overrides[validate_access_token] = lambda: {"sub": "test-user-id"}
    yield
    app.dependency_overrides.clear()


def test_record_exception_writes_full_context_to_repository():
    """RecordException must thread every field through to QuoteProcessingRepository.record_exception."""
    fake_repository = MagicMock()
    fake_repository.record_exception.return_value = uuid4()
    service = QuoteProcessingResultService(processing_repository=fake_repository)

    processing_id = uuid4()
    line_item_id = uuid4()
    from app.modules.quote_processing.schemas import RecordExceptionRequest

    req = RecordExceptionRequest(
        processing_id=processing_id,
        line_item_id=line_item_id,
        agent_name="pricing_agent",
        tool_name="PriceBreakTool",
        exception_code="PRICE_MISMATCH",
        exception_message="Quoted price does not match IQMS price break",
        retry_attempt=1,
        max_retry_count=3,
        is_retryable=True,
    )
    result = service.record_exception(req)

    fake_repository.record_exception.assert_called_once_with(
        processing_id=processing_id,
        agent_name="pricing_agent",
        tool_name="PriceBreakTool",
        exception_code="PRICE_MISMATCH",
        exception_message="Quoted price does not match IQMS price break",
        line_item_id=line_item_id,
        retry_attempt=1,
        max_retry_count=3,
        is_retryable=True,
        resolved=False,
        resolved_by=None,
        resolved_time=None,
    )
    assert result.processing_id == processing_id
    assert result.line_item_id == line_item_id
    assert result.agent_name == "pricing_agent"
    assert result.is_retryable is True


def test_record_exception_endpoint(monkeypatch):
    fake_service = MagicMock()
    processing_id = str(uuid4())
    exception_id = str(uuid4())
    from app.modules.quote_processing.schemas import ExceptionRecordData
    from datetime import datetime, timezone

    fake_service.record_exception.return_value = ExceptionRecordData(
        exception_id=exception_id,
        processing_id=processing_id,
        line_item_id=None,
        agent_name="extraction",
        tool_name="PDFExtractor",
        exception_code="EX-002",
        exception_message="Missing required header field",
        retry_attempt=0,
        max_retry_count=2,
        is_retryable=False,
        resolved=False,
        resolved_by=None,
        resolved_time=None,
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(quote_processing_router, "quote_processing_result_service", fake_service)

    response = client.post(
        "/quote-processing/record-exception",
        json={
            "processing_id": processing_id,
            "agent_name": "extraction",
            "tool_name": "PDFExtractor",
            "exception_code": "EX-002",
            "exception_message": "Missing required header field",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["data"]["exception_code"] == "EX-002"
    assert data["data"]["agent_name"] == "extraction"
    fake_service.record_exception.assert_called_once()


def test_record_exception_requires_auth():
    app.dependency_overrides.clear()
    response = client.post(
        "/quote-processing/record-exception",
        json={
            "processing_id": str(uuid4()),
            "agent_name": "extraction",
            "tool_name": "PDFExtractor",
            "exception_code": "EX-002",
            "exception_message": "Missing required header field",
        },
    )
    assert response.status_code == 401


def test_move_quote_file_success_goes_to_to_folder(tmp_path):
    source_folder = tmp_path / "incoming"
    destination_folder = tmp_path / "processed"
    source_folder.mkdir()

    quote_file = source_folder / "quote123.pdf"
    quote_file.write_bytes(b"pdf")

    service = QuoteProcessingResultService()
    result = service.move_quote_file(
        MoveQuoteFileRequest(
            filename="quote123.pdf",
            from_folder=str(source_folder),
            to_folder=str(destination_folder),
            outcome="SUCCESS",
        )
    )

    assert result.moved is True
    assert result.outcome == "SUCCESS"
    assert not quote_file.exists()
    assert (destination_folder / "quote123.pdf").exists()


def test_move_quote_file_failed_goes_to_to_folder(tmp_path):
    source_folder = tmp_path / "incoming"
    destination_folder = tmp_path / "exceptions"
    source_folder.mkdir()

    quote_file = source_folder / "quote456.pdf"
    quote_file.write_bytes(b"pdf")

    service = QuoteProcessingResultService()
    result = service.move_quote_file(
        MoveQuoteFileRequest(
            filename="quote456.pdf",
            from_folder=str(source_folder),
            to_folder=str(destination_folder),
            outcome="FAILED",
        )
    )

    assert result.moved is True
    assert (destination_folder / "quote456.pdf").exists()


def test_move_quote_file_missing_source_returns_404(tmp_path):
    source_folder = tmp_path / "incoming"
    source_folder.mkdir()

    service = QuoteProcessingResultService()
    with pytest.raises(Exception) as exc_info:
        service.move_quote_file(
            MoveQuoteFileRequest(
                filename="does-not-exist.pdf",
                from_folder=str(source_folder),
                to_folder=str(tmp_path / "processed"),
                outcome="SUCCESS",
            )
        )
    assert getattr(exc_info.value, "code", None) == "QUOTE_FILE_NOT_FOUND"


def test_move_quote_file_creates_destination_folder_if_missing(tmp_path):
    source_folder = tmp_path / "incoming"
    destination_folder = tmp_path / "does" / "not" / "exist" / "yet"
    source_folder.mkdir()
    quote_file = source_folder / "quote789.pdf"
    quote_file.write_bytes(b"pdf")

    service = QuoteProcessingResultService()
    result = service.move_quote_file(
        MoveQuoteFileRequest(
            filename="quote789.pdf",
            from_folder=str(source_folder),
            to_folder=str(destination_folder),
            outcome="PARTIAL",
        )
    )

    assert result.moved is True
    assert (destination_folder / "quote789.pdf").exists()


def test_move_quote_file_endpoint(monkeypatch):
    fake_service = MagicMock()
    from app.modules.quote_processing.schemas import MoveQuoteFileResponseData

    fake_service.move_quote_file.return_value = MoveQuoteFileResponseData(
        filename="quote123.pdf",
        outcome="SUCCESS",
        source_path="C:/incoming/quote123.pdf",
        destination_path="C:/processed/quote123.pdf",
        moved=True,
    )
    monkeypatch.setattr(quote_processing_router, "quote_processing_result_service", fake_service)

    response = client.post(
        "/quote-processing/move-quote-file",
        json={
            "filename": "quote123.pdf",
            "from_folder": "C:/incoming",
            "to_folder": "C:/processed",
            "outcome": "SUCCESS",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["moved"] is True
    fake_service.move_quote_file.assert_called_once()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("June 15th, 2026", date(2026, 6, 15)),
        ("August 22nd, 2025", date(2025, 8, 22)),
        ("October 14th, 2025", date(2025, 10, 14)),
        ("November 30th, 2025", date(2025, 11, 30)),
        ("July 6, 2026", date(2026, 7, 6)),
        ("February 13, 2026", date(2026, 2, 13)),
        ("6/15/2026", date(2026, 6, 15)),
        ("2026-06-15", date(2026, 6, 15)),
        ("  July   6,  2026 ", date(2026, 7, 6)),
        (None, None),
        ("", None),
        ("see spreadsheet", None),
    ],
)
def test_parse_date_handles_ordinal_suffixes(raw, expected):
    from app.database.quote_processing_repository import QuoteProcessingRepository

    assert QuoteProcessingRepository._parse_date(raw) == expected
