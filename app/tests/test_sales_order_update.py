"""Tests for sales order detail update endpoint."""
from unittest.mock import MagicMock, patch
import pytest
from datetime import datetime, timezone

from app.modules.sales_order.service import SalesOrderService
from app.modules.sales_order.schemas import UpdateSalesOrderDetailsResponse
from app.core.exceptions import BusinessException


def test_update_sales_order_details_service_success():
    """Test update_sales_order_details service method with successful update."""
    mock_repo = MagicMock()
    mock_repo.update_sales_order_detail.return_value = {
        "sales_order_detail_id": 1,
        "unit_price": 25.50,
        "on_hold": False,
        "ship_hold": True,
        "updated_at": datetime.now(timezone.utc),
    }

    service = SalesOrderService(sync_repository=mock_repo)
    result = service.update_sales_order_details(
        sales_order_detail_id=1,
        unit_price=25.50,
        on_hold=False,
        ship_hold=True,
    )

    assert isinstance(result, UpdateSalesOrderDetailsResponse)
    assert result.sales_order_detail_id == 1
    assert result.unit_price == 25.50
    assert result.on_hold is False
    assert result.ship_hold is True

    mock_repo.update_sales_order_detail.assert_called_once_with(
        sales_order_detail_id=1,
        unit_price=25.50,
        on_hold=False,
        ship_hold=True,
    )


def test_update_sales_order_details_service_partial_update():
    """Test partial update - only price."""
    mock_repo = MagicMock()
    updated_at = datetime.now(timezone.utc)
    mock_repo.update_sales_order_detail.return_value = {
        "sales_order_detail_id": 1,
        "unit_price": 30.00,
        "on_hold": True,
        "ship_hold": False,
        "updated_at": updated_at,
    }

    service = SalesOrderService(sync_repository=mock_repo)
    result = service.update_sales_order_details(
        sales_order_detail_id=1,
        unit_price=30.00,
        on_hold=None,
        ship_hold=None,
    )

    assert result.unit_price == 30.00
    mock_repo.update_sales_order_detail.assert_called_once_with(
        sales_order_detail_id=1,
        unit_price=30.00,
        on_hold=None,
        ship_hold=None,
    )


def test_update_sales_order_details_service_not_found():
    """Test update when sales order detail is not found."""
    mock_repo = MagicMock()
    mock_repo.update_sales_order_detail.return_value = None

    service = SalesOrderService(sync_repository=mock_repo)

    with pytest.raises(BusinessException) as exc_info:
        service.update_sales_order_details(
            sales_order_detail_id=999,
            unit_price=25.50,
        )

    assert exc_info.value.code == "SALES_ORDER_DETAIL_NOT_FOUND"
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.message.lower()


def test_update_sales_order_details_service_only_holds():
    """Test updating only hold flags."""
    mock_repo = MagicMock()
    updated_at = datetime.now(timezone.utc)
    mock_repo.update_sales_order_detail.return_value = {
        "sales_order_detail_id": 5,
        "unit_price": 100.00,
        "on_hold": True,
        "ship_hold": True,
        "updated_at": updated_at,
    }

    service = SalesOrderService(sync_repository=mock_repo)
    result = service.update_sales_order_details(
        sales_order_detail_id=5,
        on_hold=True,
        ship_hold=True,
    )

    assert result.on_hold is True
    assert result.ship_hold is True
    mock_repo.update_sales_order_detail.assert_called_once_with(
        sales_order_detail_id=5,
        unit_price=None,
        on_hold=True,
        ship_hold=True,
    )
