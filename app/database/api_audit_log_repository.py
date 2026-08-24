"""Persistence for the api_audit_logs table - logs every outbound IQMS API call."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from app.database.connection import DatabaseConnection


class ApiAuditLogRepository:
    def __init__(self, connection: DatabaseConnection | None = None) -> None:
        self.connection = connection or DatabaseConnection()

    def log_call(
        self,
        endpoint: str,
        http_method: str,
        status: str,
        http_status: Optional[int] = None,
        duration_ms: Optional[int] = None,
        request_payload: Any = None,
        response_payload: Any = None,
        retry_attempt: int = 0,
        processing_id: Optional[UUID] = None,
        line_item_id: Optional[UUID] = None,
    ) -> UUID:
        """Insert one api_audit_logs row. agent_name/tool_name are intentionally left unset."""
        api_log_id = uuid4()
        with self.connection.connect() as db, db.cursor() as cursor:
            cursor.execute(
                """INSERT INTO api_audit_logs (api_log_id, processing_id, line_item_id, endpoint,
                   http_method, request_payload, response_payload, http_status, retry_attempt,
                   duration_ms, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)""",
                (
                    api_log_id,
                    processing_id,
                    line_item_id,
                    endpoint,
                    http_method,
                    json.dumps(request_payload) if request_payload is not None else None,
                    json.dumps(response_payload) if response_payload is not None else None,
                    http_status,
                    retry_attempt,
                    duration_ms,
                    status,
                    datetime.now(timezone.utc),
                ),
            )
        return api_log_id
