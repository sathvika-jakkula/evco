"""
ReportingService: assembles the Quote Execution Summary data for a
processing_id. Returns structured data only - no email text, no subject
line, no call to NotificationService. The calling agent formats and sends
the report itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.core.exceptions import BusinessException
from app.database.aka_resolution_repository import AkaResolutionRepository
from app.database.line_item_audit_repository import LineItemAuditRepository
from app.database.pricing_history_repository import PricingHistoryRepository
from app.database.pricing_resolution_repository import PricingResolutionRepository
from app.database.quote_scan_repository import QuoteScanRepository
from app.database.review_row_repository import ReviewRowRepository
from app.database.sales_order_resolution_repository import SalesOrderResolutionRepository
from app.modules.reporting.client_report import build_quote_report
from app.modules.reporting.client_workbook import build_client_workbook_with_sheets
from app.modules.reporting.schemas import (
    ClientReportSource,
    ReviewRowEntry,
    AkaResolution,
    LineItemSummary,
    PricingResolution,
    PricingTier,
    QuoteSummaryData,
    SalesOrderFact,
    SalesOrderResolution,
)


DEFAULT_REPORT_FILE_NAME = "EVCO_Review_Per_Quote.xlsx"
CLIENT_REPORT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class ClientReportFile:
    file_name: str
    content: bytes
    sheet_names: list[str]
    processing_ids: list[UUID]


class ReportingService:
    def __init__(
        self,
        line_item_audit_repository: Optional[LineItemAuditRepository] = None,
        aka_resolution_repository: Optional[AkaResolutionRepository] = None,
        pricing_resolution_repository: Optional[PricingResolutionRepository] = None,
        sales_order_resolution_repository: Optional[SalesOrderResolutionRepository] = None,
        pricing_history_repository: Optional[PricingHistoryRepository] = None,
        review_row_repository: Optional[ReviewRowRepository] = None,
        scan_repository: Optional[QuoteScanRepository] = None,
    ) -> None:
        self.line_item_audit_repository = line_item_audit_repository or LineItemAuditRepository()
        self.aka_resolution_repository = aka_resolution_repository or AkaResolutionRepository()
        self.pricing_resolution_repository = pricing_resolution_repository or PricingResolutionRepository()
        self.sales_order_resolution_repository = sales_order_resolution_repository or SalesOrderResolutionRepository()
        self.pricing_history_repository = pricing_history_repository or PricingHistoryRepository()
        self.review_row_repository = review_row_repository or ReviewRowRepository()
        self.scan_repository = scan_repository or QuoteScanRepository()

    def resolve_processing_ids(self, source: ClientReportSource) -> list[UUID]:
        """scan_ids expanded to their quotes' processing_ids, then processing_ids appended. No duplicates."""
        from_scans = self.scan_repository.get_processing_ids_for_scans(source.scan_ids) if source.scan_ids else []
        return list(dict.fromkeys([*from_scans, *source.processing_ids]))

    def build_client_report(self, source: ClientReportSource, file_name: str = DEFAULT_REPORT_FILE_NAME) -> "ClientReportFile":
        """Client review workbook for the scans and/or quotes in source."""
        processing_ids = self.resolve_processing_ids(source)
        reports = [build_quote_report(self.get_quote_summary(pid)) for pid in processing_ids]
        content, sheet_names = build_client_workbook_with_sheets(reports)
        return ClientReportFile(
            file_name=file_name,
            content=content,
            sheet_names=sheet_names,
            processing_ids=processing_ids,
        )

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

        # Pre-fetch all data in batch to avoid N+1 queries
        pricing_cache = {}
        customer_no = header.get("customer_no")
        if customer_no:
            for row in data["line_items"]:
                evco_part_id = row.get("evco_part_id")
                bom_id = row.get("bom_id")
                if evco_part_id:
                    cache_key = (customer_no, evco_part_id, bom_id)
                    if cache_key not in pricing_cache:
                        pricing_cache[cache_key] = self.pricing_history_repository.get_all_tiers(
                            customer_no, evco_part_id, bom_id
                        )

        # Batch-fetch all resolution and exception data
        line_item_ids = [row["line_item_id"] for row in data["line_items"]]
        aka_cache = self.aka_resolution_repository.get_latest_for_line_items(line_item_ids)
        pricing_cache_res = self.pricing_resolution_repository.get_latest_for_line_items(line_item_ids)
        sales_order_cache = self.sales_order_resolution_repository.get_all_for_line_items(line_item_ids)
        exception_details_cache = self.line_item_audit_repository.get_exception_details_for_line_items(line_item_ids)

        line_items = [
            self._build_line_item(row, customer_no, pricing_cache, aka_cache, pricing_cache_res, sales_order_cache, exception_details_cache)
            for row in data["line_items"]
        ]

        # Fetch quote-level exceptions (no line_item_id)
        quote_exceptions = self.line_item_audit_repository.get_quote_level_exceptions(processing_id)

        # Calculate not_processed_lines as lines with no decision and no exceptions
        not_processed_count = sum(
            1 for line in line_items
            if line.line_status is None and not line.exceptions
        )

        return QuoteSummaryData(
            processing_id=processing_id,
            scan_id=None,
            quote_number=header.get("quote_id"),
            customer_name=header.get("customer_name"),
            ar_cust_id=header.get("customer_no"),
            effective_date=str(header["effective_date"]) if header.get("effective_date") else None,
            report_id=header.get("report_id") or report_id,
            status=None,
            total_lines=header.get("total_lines") or 0,
            successful_lines=header.get("successful_lines") or 0,
            review_required_lines=header.get("review_required_lines") or 0,
            failed_lines=header.get("failed_lines") or 0,
            not_processed_lines=not_processed_count,
            exception_count=header.get("exception_count") or 0,
            quote_exceptions=quote_exceptions,
            line_items=line_items,
            review_rows=self._build_review_rows(processing_id),
        )

    _AREA_ORDER = {"AKA": 0, "PRICING": 1, "SALES_ORDER": 2}

    def _build_review_rows(self, processing_id: UUID) -> list[ReviewRowEntry]:
        entries = [
            ReviewRowEntry(
                review_row_id=row["review_row_id"],
                line_item_id=row["line_item_id"],
                line_number=row.get("line_number"),
                review_area=row["review_area"],
                evco_part_number=row.get("evco_part_number"),
                mold_number=row.get("mold_number"),
                manufacturing_bom_number=row.get("manufacturing_bom_number"),
                customer_number=row.get("customer_number"),
                created_at=row["created_at"],
                rule_code=row["rule_code"],
                check=row["check_name"],
                status=row["status"],
                message=row["message"],
                field=row.get("field"),
                record_ref=row.get("record_ref"),
                sales_order_id=row.get("sales_order_id"),
                sales_order_detail_id=row.get("sales_order_detail_id"),
                release_id=row.get("release_id"),
                quote_value=row.get("quote_value"),
                system_value=row.get("system_value"),
                proposed_value=row.get("proposed_value"),
                exception_id=row.get("exception_id"),
            )
            for row in self.review_row_repository.get_rows_for_processing(processing_id)
        ]
        entries.sort(key=lambda e: (e.line_number is None, e.line_number or 0, self._AREA_ORDER.get(e.review_area, 9)))
        return entries

    def _build_line_item(
        self, row: dict, customer_no: Optional[str], pricing_cache: dict,
        aka_cache: dict, pricing_cache_res: dict, sales_order_cache: dict, exception_details_cache: dict
    ) -> LineItemSummary:
        line_item_id = row["line_item_id"]

        aka_row = aka_cache.get(line_item_id)
        aka = AkaResolution(
            decision=aka_row.get("decision") if aka_row else None,
            aka_id=aka_row.get("aka_id") if aka_row else row.get("aka_id"),
            before=aka_row.get("before_value") if aka_row else None,
            after=aka_row.get("after_value") if aka_row else None,
        )

        pricing_row = pricing_cache_res.get(line_item_id)
        tiers: list[PricingTier] = []
        if customer_no and row.get("evco_part_id"):
            cache_key = (customer_no, row["evco_part_id"], row.get("bom_id"))
            tier_rows = pricing_cache.get(cache_key, [])
            # Use pricing_row to get before price if available
            before_price = float(pricing_row["pricing_before_value"]) if pricing_row and pricing_row.get("pricing_before_value") is not None else None

            for tier_row in tier_rows:
                # For active tiers: current_price is the active price, previous_price is before (if updated)
                # For inactive tiers: current_price was what it was when active, previous_price is N/A
                previous = before_price if tier_row["is_active"] and before_price is not None else None
                tiers.append(PricingTier(
                    quantity=tier_row["quantity"],
                    previous_price=previous,
                    current_price=float(tier_row["price"]),
                    is_active=tier_row["is_active"],
                    inactive_date=str(tier_row["inactive_date"]) if tier_row.get("inactive_date") else None,
                    created_by_line_item_id=tier_row.get("created_by_line_item_id"),
                    inactivated_by_line_item_id=tier_row.get("inactivated_by_line_item_id"),
                ))
        pricing = PricingResolution(
            decision=pricing_row.get("decision") if pricing_row else None,
            tiers=tiers,
        )

        so_rows = sales_order_cache.get(line_item_id, [])
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

        # Get exception details from batch cache
        exception_details = exception_details_cache.get(line_item_id, [])
        exception_codes = [exc.get("exception_code") for exc in exception_details if exc.get("exception_code")]

        return LineItemSummary(
            line_item_id=line_item_id,
            line_number=row.get("line_number"),
            evco_mfg_number=row.get("bom_id"),
            evco_part_id=row.get("evco_part_id"),
            customer_part_number=row.get("customer_part_number"),
            line_status=row.get("line_status"),
            aka=aka,
            pricing=pricing,
            sales_order=sales_order,
            exceptions=exception_codes,
            exception_details=exception_details,
        )
