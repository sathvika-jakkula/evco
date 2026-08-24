from unittest.mock import MagicMock, patch
import json
import pytest
from uuid import uuid4

from app.integrations.iqms import IQMSClient
from app.modules.customer.models import CustomerRecord
from app.modules.customer.repository import CustomerRepository
from app.modules.customer.service import CustomerService
from app.modules.customer.schemas import CustomerResolveRequest, MatchStatusEnum


def test_iqms_client_login_success():
    client = IQMSClient(
        base_url="https://evco.iqtrain.iqms-cloud.net/WebAPI",
        app_name="WebAPI",
        username="AIAPI",
        password="Airwaves8#",
    )

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"AuthToken": "test-token-12345"}).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        token = client.login()
        assert token == "test-token-12345"
        assert client.get_auth_token() == "test-token-12345"


def test_iqms_client_login_timeout():
    import urllib.error
    import socket

    client = IQMSClient(base_url="https://evco.iqtrain.iqms-cloud.net/WebAPI")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(socket.timeout("timed out"))):
        token = client.login()
        assert token is None


def test_iqms_client_get_customers_lite():
    client = IQMSClient(auth_token="valid-token")

    sample_data = [
        {
            "ID": 101,
            "Company": "ACME CORP",
            "CustNo": "ACME-001",
            "Addr1": "123 Main St",
            "City": "Chicago",
            "State": "IL",
        },
        {
            "ID": 102,
            "Company": "GLOBAL LOGISTICS",
            "CustNo": "GLOB-001",
            "Addr1": "456 World Way",
            "City": "New York",
            "State": "NY",
        },
    ]

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps(sample_data).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        customers = client.get_customers_lite()
        assert len(customers) == 2
        assert customers[0]["Company"] == "ACME CORP"


def test_customer_repository_matching():
    mock_client = MagicMock(spec=IQMSClient)
    mock_client.get_customers_lite.return_value = [
        {
            "ID": 501,
            "Company": "CLACK CORPORATION",
            "CustNo": "CLACK-001",
            "Addr1": "Sturgis, MI",
        },
        {
            "ID": 502,
            "Company": "CLACK CORPORATION - MIDWEST",
            "CustNo": "CLACK-002",
            "Addr1": "Windsor, WI",
        },
        {
            "ID": 503,
            "Company": "REMEL INC",
            "CustNo": "REMEL-001",
            "Addr1": "Lenexa, KS",
        },
    ]

    repo = CustomerRepository(iqms_client=mock_client)

    # 1. Test Customer Number Match
    res_num = repo.find_candidates(customer_name="CLACK", customer_number="CLACK-002")
    assert len(res_num) == 1
    assert res_num[0].ar_custo_id == 502

    # 2. Test Exact Name Match
    res_exact = repo.find_candidates(customer_name="REMEL INC")
    assert len(res_exact) == 1
    assert res_exact[0].ar_custo_id == 503

    # 3. Test Substring Match (Ambiguous)
    res_sub = repo.find_candidates(customer_name="CLACK")
    assert len(res_sub) == 2

    # 4. Test Not Found
    res_none = repo.find_candidates(customer_name="NON EXISTENT CORP")
    assert len(res_none) == 0


def test_customer_repository_normalizes_hyphenated_customer_names():
    mock_client = MagicMock(spec=IQMSClient)
    mock_client.get_customers_lite.return_value = [
        {"ID": 777, "Company": "THE COCA-COLA COMPANY", "CustNo": "COCA-001"},
    ]
    repo = CustomerRepository(iqms_client=mock_client)

    matches = repo.find_candidates(customer_name="The Coca Cola Company")

    assert len(matches) == 1
    assert matches[0].customer_number == "COCA-001"


def test_customer_service_resolve():
    mock_client = MagicMock(spec=IQMSClient)
    mock_client.get_customers_lite.return_value = [
        {
            "ID": 801,
            "Company": "THERMO FISHER SCIENTIFIC",
            "CustNo": "THERMO-001",
            "Addr1": "Waltham, MA",
        }
    ]

    repo = CustomerRepository(iqms_client=mock_client)
    processing_repository = MagicMock()
    processing_repository.save_unique_customer_resolution_for_processing.return_value = uuid4()
    service = CustomerService(repository=repo, processing_repository=processing_repository)

    req = CustomerResolveRequest(processing_id=uuid4(), customer_name="THERMO FISHER SCIENTIFIC")
    response = service.resolve_customer(req)

    assert response.statusCode == 200
    assert response.data is not None
    assert response.data.match_status == MatchStatusEnum.UNIQUE
    assert len(response.data.candidates) == 1
    assert response.data.candidates[0].ar_custo_id == 801
    assert response.data.customer_resolution_id is not None
    processing_repository.save_unique_customer_resolution_for_processing.assert_called_once()
    processing_repository.save_audit_customer_number.assert_called_once_with(
        processing_id=req.processing_id,
        customer_number="THERMO-001",
    )


def test_customer_service_records_ambiguous_customer_exception():
    mock_client = MagicMock(spec=IQMSClient)
    mock_client.get_customers_lite.return_value = [
        {"ID": 1, "Company": "ACME NORTH", "CustNo": "ACME-N"},
        {"ID": 2, "Company": "ACME SOUTH", "CustNo": "ACME-S"},
    ]
    processing_repository = MagicMock()
    processing_repository.save_customer_exceptions_for_processing.return_value = [uuid4(), uuid4()]
    service = CustomerService(
        repository=CustomerRepository(iqms_client=mock_client),
        processing_repository=processing_repository,
    )

    processing_id = uuid4()
    response = service.resolve_customer(CustomerResolveRequest(processing_id=processing_id, customer_name="ACME"))

    assert response.data is not None
    assert response.data.match_status == MatchStatusEnum.AMBIGUOUS
    assert response.data.exception_codes == ["EX-006"]
    assert response.data.exception_ids
    assert response.data.customer_resolution_id is None
    processing_repository.save_customer_exceptions_for_processing.assert_called_once()
