from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request
import urllib.error

from app.core.config import settings
from app.database.api_audit_log_repository import ApiAuditLogRepository

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying (transient server-side/rate-limit failures).
# 401/403 are handled separately since they trigger a re-login, not a plain retry.
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def _default_unwrap(data: Any) -> List[Dict[str, Any]]:
    """Default response-list unwrapping shared by most IQMS endpoints."""
    if isinstance(data, dict):
        return data.get("data") or data.get("Data") or data.get("items") or []
    if isinstance(data, list):
        return data
    return []


class IQMSClient:
    """Client for connecting to DELMIAWorks / IQMS WebAPI."""

    DEFAULT_MAX_RETRIES = 3  # retries beyond the first attempt (4 attempts total)

    def __init__(
        self,
        base_url: Optional[str] = None,
        app_name: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: int = 5,
        audit_repository: Optional[ApiAuditLogRepository] = None,
    ) -> None:
        self.base_url = (base_url or settings.IQMS_BASE_URL).rstrip("/")
        self.app_name = app_name or settings.IQMS_APPLICATION_NAME
        self.username = username or settings.IQMS_USERNAME
        self.password = password or settings.IQMS_PASSWORD
        self._auth_token = auth_token or settings.IQMS_AUTH_TOKEN or None
        self.timeout = timeout
        self.audit_repository = audit_repository or ApiAuditLogRepository()

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

    @staticmethod
    def _safe_json(body_bytes: bytes) -> Any:
        try:
            return json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return {"raw": body_bytes.decode("utf-8", errors="replace")[:2000]}

    def _audit(
        self,
        endpoint: str,
        http_method: str,
        status: str,
        http_status: Optional[int],
        duration_ms: int,
        url: str,
        body_bytes: bytes,
        attempt: int,
    ) -> None:
        """Best-effort write to api_audit_logs - never lets logging failures break an IQMS call."""
        try:
            self.audit_repository.log_call(
                endpoint=endpoint,
                http_method=http_method,
                status=status,
                http_status=http_status,
                duration_ms=duration_ms,
                request_payload={"url": url},
                response_payload=self._safe_json(body_bytes),
                retry_attempt=attempt,
            )
        except Exception as exc:
            logger.warning("Failed to write api_audit_logs entry for %s: %s", endpoint, exc)

    def _request_with_audit(
        self, url: str, headers: Dict[str, str], method: str, endpoint_label: str, attempt: int
    ) -> Tuple[int, bytes]:
        """
        Perform one HTTP request and log it to api_audit_logs regardless of outcome
        (success, HTTP error, or network-level failure). Re-raises the original
        exception after logging, so callers' retry handling is unaffected.
        """
        started = time.perf_counter()
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body_bytes = response.read()
                duration_ms = int((time.perf_counter() - started) * 1000)
                self._audit(endpoint_label, method, "SUCCESS", response.status, duration_ms, url, body_bytes, attempt)
                return response.status, body_bytes
        except urllib.error.HTTPError as http_err:
            duration_ms = int((time.perf_counter() - started) * 1000)
            error_body = http_err.read()
            self._audit(endpoint_label, method, "FAILED", http_err.code, duration_ms, url, error_body, attempt)
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout) as net_err:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._audit(endpoint_label, method, "FAILED", None, duration_ms, url, str(net_err).encode("utf-8"), attempt)
            raise

    def _fetch_json_list(
        self,
        url: str,
        label: str,
        unwrap: Optional[Callable[[Any], List[Dict[str, Any]]]] = None,
        max_retries: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        GET url and return the unwrapped JSON list, with retries and an
        api_audit_logs entry for every attempt.

        Retries (up to max_retries additional attempts) on:
          - HTTP 401/403 - re-authenticates before retrying
          - HTTP 429/500/502/503/504 - transient server-side failures, retried with backoff
          - network-level errors (timeouts, connection failures) - retried with backoff
        Any other HTTP error status, or an unexpected exception (including a
        malformed/undecodable response body), is treated as non-retryable and
        returns [] immediately.
        """
        unwrap = unwrap or _default_unwrap
        max_retries = self.DEFAULT_MAX_RETRIES if max_retries is None else max_retries
        token = self.get_auth_token()

        for attempt in range(max_retries + 1):
            if not token:
                logger.error("No valid IQMS AuthToken available for %s request.", label)
                return []

            headers = {
                "Accept": "application/json",
                "AuthToken": token,
                "Authorization": f"Bearer {token}",
                "User-Agent": "EVCO-Backend/1.0",
            }

            try:
                status_code, body_bytes = self._request_with_audit(url, headers, "GET", label, attempt)
                if status_code != 200:
                    logger.error("IQMS %s returned unexpected status %s", label, status_code)
                    return []
                try:
                    return unwrap(json.loads(body_bytes.decode("utf-8")))
                except Exception as parse_exc:
                    logger.error("Failed to parse IQMS %s response: %s", label, parse_exc)
                    return []
            except urllib.error.HTTPError as http_err:
                has_retries_left = attempt < max_retries
                if http_err.code in (401, 403) and has_retries_left:
                    logger.warning(
                        "IQMS AuthToken expired or invalid (HTTP %s) on %s attempt %d/%d - re-authenticating...",
                        http_err.code, label, attempt + 1, max_retries + 1,
                    )
                    self._auth_token = None
                    token = self.login()
                    continue
                if http_err.code in RETRYABLE_HTTP_STATUSES and has_retries_left:
                    delay = min(2 ** attempt, 5)
                    logger.warning(
                        "IQMS %s returned HTTP %s on attempt %d/%d - retrying in %ss...",
                        label, http_err.code, attempt + 1, max_retries + 1, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("IQMS %s HTTP error %s: %s", label, http_err.code, http_err)
                return []
            except (urllib.error.URLError, TimeoutError, socket.timeout) as net_err:
                if attempt < max_retries:
                    delay = min(2 ** attempt, 5)
                    logger.warning(
                        "IQMS %s network error on attempt %d/%d: %s - retrying in %ss...",
                        label, attempt + 1, max_retries + 1, net_err, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.error("IQMS %s network error, retries exhausted: %s", label, net_err)
                return []
            except Exception as exc:
                logger.error("Unexpected error fetching IQMS %s: %s", label, exc)
                return []

        return []

    def get_customers_lite(self) -> List[Dict[str, Any]]:
        """Fetches the complete customer list from IQMS /CRM/CustomerCentral/CustomersLite."""
        url = f"{self.base_url}/CRM/CustomerCentral/CustomersLite"

        def unwrap(data: Any) -> List[Dict[str, Any]]:
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("items") or data.get("data") or data.get("Customers") or [data]
            return []

        return self._fetch_json_list(url, "CustomersLite", unwrap=unwrap)

    def get_aka_inventory_for_customer(self, ar_custo_id: int) -> List[Dict[str, Any]]:
        """
        Fetches AKA (customer alias) inventory records for a customer from
        IQMS /Manufacturing/Inventory/AKAInventoryForCustomer/{ArCustoId}.
        """
        url = f"{self.base_url}/Manufacturing/Inventory/AKAInventoryForCustomer/{ar_custo_id}"
        return self._fetch_json_list(url, "AKAInventoryForCustomer")

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
        return self._fetch_json_list(url, "SalesOrder")

    def get_sales_order_details(self, sales_order_id: int) -> List[Dict[str, Any]]:
        """Fetches sales order detail (line item) records for one sales order - filters correctly server-side."""
        url = f"{self.base_url}/SalesDistribution/SalesOrder/SalesOrderDetails?salesOrderId={sales_order_id}"
        return self._fetch_json_list(url, "SalesOrderDetails")

    def get_sales_order_releases(self, sales_order_detail_id: int) -> List[Dict[str, Any]]:
        """Fetches release/shipment schedule records for one sales order detail line - filters correctly server-side."""
        url = (
            f"{self.base_url}/SalesDistribution/SalesOrder/SalesOrderReleases"
            f"?salesOrderDetailId={sales_order_detail_id}"
        )
        return self._fetch_json_list(url, "SalesOrderReleases")
