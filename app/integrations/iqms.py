from __future__ import annotations

import json
import logging
import socket
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request
import urllib.error

from app.core.config import settings

logger = logging.getLogger(__name__)


class IQMSClient:
    """Client for connecting to DELMIAWorks / IQMS WebAPI."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        app_name: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: int = 5,
    ) -> None:
        self.base_url = (base_url or settings.IQMS_BASE_URL).rstrip("/")
        self.app_name = app_name or settings.IQMS_APPLICATION_NAME
        self.username = username or settings.IQMS_USERNAME
        self.password = password or settings.IQMS_PASSWORD
        self._auth_token = auth_token or settings.IQMS_AUTH_TOKEN or None
        self.timeout = timeout

    def login(self) -> Optional[str]:
        """
        Authenticates against IQMS WebAPI User/Login endpoint and returns AuthToken.
        Endpoint: /User/Login?ApplicationName=...&Username=...&Password=...
        """
        params = {
            "ApplicationName": self.app_name,
            "Username": self.username,
            "Password": self.password,
        }
        query_string = urllib.parse.urlencode(params)
        url = f"{self.base_url}/User/Login?{query_string}"

        headers = {
            "Accept": "application/json",
            "User-Agent": "EVCO-Backend/1.0",
        }

        # Clear expired token before attempting login
        self._auth_token = None

        for method in ["POST", "GET"]:
            try:
                req = urllib.request.Request(url, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    if response.status == 200:
                        body_bytes = response.read()
                        data = json.loads(body_bytes.decode("utf-8"))
                        token = data.get("AuthToken")
                        if token:
                            self._auth_token = token
                            logger.info("Successfully authenticated with IQMS WebAPI.")
                            return token
            except (TimeoutError, urllib.error.URLError) as net_err:
                # If network connection times out, do not retry secondary HTTP method
                reason = getattr(net_err, "reason", net_err)
                if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(net_err).lower():
                    logger.error("IQMS server at %s is unreachable (connection timed out).", self.base_url)
                    return None
                logger.warning("IQMS login attempt using %s failed: %s", method, net_err)
            except Exception as exc:
                logger.warning("IQMS login attempt using %s failed: %s", method, exc)

        logger.error("Failed to authenticate with IQMS WebAPI.")
        return None

    def get_auth_token(self, force_refresh: bool = False) -> Optional[str]:
        """Returns valid AuthToken, authenticating if necessary."""
        if force_refresh or not self._auth_token:
            return self.login()
        return self._auth_token

    def get_customers_lite(self) -> List[Dict[str, Any]]:
        """
        Fetches the complete customer list from IQMS /CRM/CustomerCentral/CustomersLite.
        """
        token = self.get_auth_token()
        url = f"{self.base_url}/CRM/CustomerCentral/CustomersLite"

        for attempt in range(2):
            if not token:
                logger.error("No valid IQMS AuthToken available for CustomersLite request.")
                return []

            headers = {
                "Accept": "application/json",
                "AuthToken": token,
                "Authorization": f"Bearer {token}",
                "User-Agent": "EVCO-Backend/1.0",
            }

            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    if response.status == 200:
                        body_bytes = response.read()
                        data = json.loads(body_bytes.decode("utf-8"))
                        if isinstance(data, list):
                            return data
                        elif isinstance(data, dict):
                            return data.get("items") or data.get("data") or data.get("Customers") or [data]
                        return []
            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403) and attempt == 0:
                    logger.warning("IQMS AuthToken expired or invalid (HTTP %s). Attempting re-login...", http_err.code)
                    self._auth_token = None
                    token = self.login()
                    continue
                logger.error("IQMS CustomersLite HTTP error %s: %s", http_err.code, http_err)
                break
            except Exception as exc:
                logger.error("Error fetching IQMS CustomersLite: %s", exc)
                break

        return []

    def get_aka_inventory_for_customer(self, ar_custo_id: int) -> List[Dict[str, Any]]:
        """
        Fetches AKA (customer alias) inventory records for a customer from
        IQMS /Manufacturing/Inventory/AKAInventoryForCustomer/{ArCustoId}.
        """
        token = self.get_auth_token()
        url = f"{self.base_url}/Manufacturing/Inventory/AKAInventoryForCustomer/{ar_custo_id}"

        for attempt in range(2):
            if not token:
                logger.error("No valid IQMS AuthToken available for AKAInventoryForCustomer request.")
                return []

            headers = {
                "Accept": "application/json",
                "AuthToken": token,
                "Authorization": f"Bearer {token}",
                "User-Agent": "EVCO-Backend/1.0",
            }

            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    if response.status == 200:
                        body_bytes = response.read()
                        data = json.loads(body_bytes.decode("utf-8"))
                        if isinstance(data, dict):
                            return data.get("data") or data.get("Data") or data.get("items") or []
                        if isinstance(data, list):
                            return data
                        return []
            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403) and attempt == 0:
                    logger.warning(
                        "IQMS AuthToken expired or invalid (HTTP %s). Attempting re-login...", http_err.code
                    )
                    self._auth_token = None
                    token = self.login()
                    continue
                logger.error("IQMS AKAInventoryForCustomer HTTP error %s: %s", http_err.code, http_err)
                break
            except Exception as exc:
                logger.error("Error fetching IQMS AKAInventoryForCustomer for ArCustoId=%s: %s", ar_custo_id, exc)
                break

        return []

    def _get_json_list(self, url: str, label: str) -> List[Dict[str, Any]]:
        """Shared GET-and-unwrap-list logic with the standard AuthToken retry-once pattern."""
        token = self.get_auth_token()

        for attempt in range(2):
            if not token:
                logger.error("No valid IQMS AuthToken available for %s request.", label)
                return []

            headers = {
                "Accept": "application/json",
                "AuthToken": token,
                "Authorization": f"Bearer {token}",
                "User-Agent": "EVCO-Backend/1.0",
            }

            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    if response.status == 200:
                        body_bytes = response.read()
                        data = json.loads(body_bytes.decode("utf-8"))
                        if isinstance(data, dict):
                            return data.get("data") or data.get("Data") or data.get("items") or []
                        if isinstance(data, list):
                            return data
                        return []
            except urllib.error.HTTPError as http_err:
                if http_err.code in (401, 403) and attempt == 0:
                    logger.warning("IQMS AuthToken expired or invalid (HTTP %s). Attempting re-login...", http_err.code)
                    self._auth_token = None
                    token = self.login()
                    continue
                logger.error("IQMS %s HTTP error %s: %s", label, http_err.code, http_err)
                break
            except Exception as exc:
                logger.error("Error fetching IQMS %s: %s", label, exc)
                break

        return []

    def get_sales_orders(self) -> List[Dict[str, Any]]:
        """
        Fetches all sales order line records from IQMS
        /SalesDistribution/SalesOrder/SalesOrder.

        IQMS's `filters` query parameter (e.g. filters=ArInvtId.eq~207192)
        does not actually filter server-side - confirmed by testing: the
        response includes rows for ArInvtId values other than the one
        filtered on. So this always fetches the full list; callers must
        filter client-side (see SalesOrderService.get_sales_orders).
        """
        url = f"{self.base_url}/SalesDistribution/SalesOrder/SalesOrder"
        return self._get_json_list(url, "SalesOrder")

    def get_sales_order_details(self, sales_order_id: int) -> List[Dict[str, Any]]:
        """Fetches sales order detail (line item) records for one sales order - filters correctly server-side."""
        url = f"{self.base_url}/SalesDistribution/SalesOrder/SalesOrderDetails?salesOrderId={sales_order_id}"
        return self._get_json_list(url, "SalesOrderDetails")

    def get_sales_order_releases(self, sales_order_detail_id: int) -> List[Dict[str, Any]]:
        """Fetches release/shipment schedule records for one sales order detail line - filters correctly server-side."""
        url = (
            f"{self.base_url}/SalesDistribution/SalesOrder/SalesOrderReleases"
            f"?salesOrderDetailId={sales_order_detail_id}"
        )
        return self._get_json_list(url, "SalesOrderReleases")
