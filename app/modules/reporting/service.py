"""
ReportingService: assembles the Quote Execution Summary data for a
processing_id. Returns structured data only - no email text, no subject
line, no call to NotificationService. The calling agent formats and sends
the report itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.core.exceptions import BusinessException
from app.database.aka_resolution_repository import AkaResolutionRepository
from app.database.line_item_audit_repository import LineItemAuditRepository
from app.database.pricing_history_repository import PricingHistoryRepository
from app.database.pricing_resolution_repository import PricingResolutionRepository
from app.database.sales_order_resolution_repository import SalesOrderResolutionRepository
from app.modules.reporting.schemas import (
    AkaResolution,
    LineItemSummary,
    PricingResolution,
    PricingTier,
    QuoteSummaryData,
    SalesOrderFact,
    SalesOrderResolution,
)


class ReportingService:
    def __init__(
        self,
        line_item_audit_repository: Optional[LineItemAuditRepository] = None,
        aka_resolution_repository: Optional[AkaResolutionRepository] = None,
        pricing_resolution_repository: Optional[PricingResolutionRepository] = None,
        sales_order_resolution_repository: Optional[SalesOrderResolutionRepository] = None,
        pricing_history_repository: Optional[PricingHistoryRepository] = None,
    ) -> None:
        self.line_item_audit_repository = line_item_audit_repository or LineItemAuditRepository()
        self.aka_resolution_repository = aka_resolution_repository or AkaResolutionRepository()
        self.pricing_resolution_repository = pricing_resolution_repository or PricingResolutionRepository()
        self.sales_order_resolution_repository = sales_order_resolution_repository or SalesOrderResolutionRepository()
        self.pricing_history_repository = pricing_history_repository or PricingHistoryRepository()

    def get_quote_summary(self, processing_id: UUID) -> QuoteSummaryData:
        report_id = f"RPT-{processing_id}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        self.line_item_audit_repository.finalize_quote_audit(processing_id, report_id)
        data = self.line_item_audit_repository.get_report_data(processing_id)

        header = data["header"]
        if header is None:
            raise BusinessException(
                message=f"No quote_processing/quote_audit record found for processing_id '{processing_id}'",
                code="QUOTE_PROCESSING_NOT_FOUND",
                status_code=404,
                details={"processing_id": str(processing_id)},
            )

        line_items = [self._build_line_item(row, header.get("customer_no")) for row in data["line_items"]]

        return QuoteSummaryData(
            processing_id=processing_id,
            quote_number=header.get("quote_id"),
            status=None,  # not tracked at the quote_audit level today - left unset rather than guessed
            effective_date=str(header["effective_date"]) if header.get("effective_date") else None,
            customer_name=header.get("customer_name"),
            ar_cust_id=header.get("customer_no"),
            report_id=header.get("report_id") or report_id,
            total_lines=header.get("total_lines") or 0,
            successful_lines=header.get("successful_lines") or 0,
            review_required_lines=header.get("review_required_lines") or 0,
            failed_lines=header.get("failed_lines") or 0,
            exception_count=header.get("exception_count") or 0,
            line_items=line_items,
        )

    def _build_line_item(self, row: dict, customer_no: Optional[str]) -> LineItemSummary:
        line_item_id = row["line_item_id"]

        aka_row = self.aka_resolution_repository.get_latest_for_line_item(line_item_id)
        aka = AkaResolution(
            decision=aka_row.get("decision") if aka_row else None,
            aka_id=aka_row.get("aka_id") if aka_row else row.get("aka_id"),
            before=aka_row.get("before_value") if aka_row else None,
            after=aka_row.get("after_value") if aka_row else None,
        )

        pricing_row = self.pricing_resolution_repository.get_latest_for_line_item(line_item_id)
        tiers: list[PricingTier] = []
        if customer_no and row.get("evco_part_id"):
            for tier_row in self.pricing_history_repository.get_all_tiers(
                customer_no, row["evco_part_id"], row.get("bom_id")
            ):
                tiers.append(PricingTier(
                    quantity=tier_row["quantity"], price=float(tier_row["price"]),
                    is_active=tier_row["is_active"],
                    inactive_date=str(tier_row["inactive_date"]) if tier_row.get("inactive_date") else None,
                    created_by_line_item_id=tier_row.get("created_by_line_item_id"),
                    inactivated_by_line_item_id=tier_row.get("inactivated_by_line_item_id"),
                ))
        pricing = PricingResolution(
            decision=pricing_row.get("decision") if pricing_row else None,
            before_price=float(pricing_row["pricing_before_value"]) if pricing_row and pricing_row.get("pricing_before_value") is not None else None,
            after_price=float(pricing_row["pricing_after_value"]) if pricing_row and pricing_row.get("pricing_after_value") is not None else None,
            tiers=tiers,
        )

        so_rows = self.sales_order_resolution_repository.get_all_for_line_item(line_item_id)
        so_facts = [
            SalesOrderFact(sales_order_id=r.get("sales_order_id"), decision=r.get("decision"), note=r.get("sales_order_note"))
            for r in so_rows
        ]
        overall_so_decision = None
        if so_facts:
            overall_so_decision = "MISMATCH" if any(f.decision == "MISMATCH" for f in so_facts) else (
                "NOT_FOUND" if all(f.decision == "NOT_FOUND" for f in so_facts) else "NO_CHANGE"
            )
        sales_order = SalesOrderResolution(decision=overall_so_decision, orders=so_facts)

        exceptions = self.line_item_audit_repository.get_exceptions_for_line_item(line_item_id)

        return LineItemSummary(
            line_item_id=line_item_id,
            evco_mfg_number=row.get("bom_id"),
            evco_part_id=row.get("evco_part_id"),
            customer_part_number=row.get("customer_part_number"),
            aka=aka,
            pricing=pricing,
            sales_order=sales_order,
            exceptions=exceptions,
            line_status=row.get("line_status"),
        )
