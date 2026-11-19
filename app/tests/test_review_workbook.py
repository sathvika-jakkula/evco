"""Stage and outcome rules and the quote-sheet layout of the review workbook (no database)."""

from datetime import datetime
from uuid import uuid4

from app.modules.reporting.review_workbook import QUOTE_HEADERS, evaluate, latest_run_per_quote, quote_rows

T0 = datetime(2026, 11, 6, 6, 0)


def _run(lines=2, exceptions=(), reviews=(), created_at=T0, quote_id="65150-003"):
    processing_id = uuid4()
    line_rows = [
        {
            "line_item_id": uuid4(), "processing_id": processing_id, "line_number": n + 1,
            "mold_number": "M1", "evco_part_id": "E1", "bom_id": "B1", "customer_part_number": "C1",
            "description": "Part", "parts": 25, "moq": 10, "price": 1.5,
        }
        for n in range(lines)
    ]
    run = {
        "processing_id": processing_id, "quote_id": quote_id, "filename": "q.pdf",
        "created_at": created_at, "completed_at": None, "scan_started_at": None,
        "customer_name": "Acme", "customer_no": "11515", "effective_date": None,
        "lines": line_rows, "exceptions": [], "reviews": [],
    }
    for exc in exceptions:
        run["exceptions"].append({
            "processing_id": processing_id, "line_item_id": None, "agent_name": exc[0],
            "exception_code": "EX-001", "exception_message": "msg", "created_at": T0,
        } if exc[1] is None else {
            "processing_id": processing_id, "line_item_id": line_rows[exc[1]]["line_item_id"],
            "agent_name": exc[0], "exception_code": "EX-001", "exception_message": "msg", "created_at": T0,
        })
    for area, status, line_index in reviews:
        run["reviews"].append({
            "processing_id": processing_id, "line_item_id": line_rows[line_index]["line_item_id"],
            "line_number": line_index + 1, "review_area": area, "rule_code": "VR-001",
            "check_name": "check", "status": status, "message": "m", "field": "f",
            "system_value": "s", "proposed_value": "p", "created_at": T0,
        })
    return run


def test_extraction_failure_stops_at_a1():
    run = evaluate(_run(exceptions=[("ExtractionAgent", None)]))
    assert run["outcome"] == "Fail"
    assert run["first_stage"] == "A1"
    assert run["outcome_text"] == "Stopped - A1"


def test_some_lines_fail_is_partial():
    run = evaluate(_run(lines=2, reviews=[("PRICING", "UPDATE_REQUIRED", 0)]))
    assert run["outcome"] == "Partial"


def test_all_lines_fail_is_fail_at_first_line_stage():
    run = evaluate(_run(lines=2, reviews=[("AKA", "NOT_VERIFIABLE", 0), ("AKA", "UPDATE_REQUIRED", 1)]))
    assert run["outcome"] == "Fail"
    assert run["first_stage"] == "A3"


def test_no_failing_rows_is_pass():
    run = evaluate(_run(lines=2, reviews=[("AKA", "NO_ACTION", 0)]))
    assert run["outcome"] == "Pass"
    assert run["last_agents"] == ""


def test_quote_rows_match_header_width():
    run = evaluate(_run(lines=1, reviews=[("AKA", "UPDATE_REQUIRED", 0)]))
    rows = quote_rows(run)
    assert rows and all(len(row) == len(QUOTE_HEADERS) for row in rows)


def test_business_rule_status_uses_report_labels():
    run = evaluate(_run(lines=3, reviews=[
        ("AKA", "NO_ACTION", 0), ("AKA", "UPDATE_REQUIRED", 1), ("AKA", "NOT_VERIFIABLE", 2)]))
    column = QUOTE_HEADERS.index("Business Rule Status")
    assert [row[column] for row in quote_rows(run)] == ["Validated", "Action Required", "Warning"]


def test_stages_after_a3_failure_are_not_run_when_agent_never_called_them():
    run = evaluate(_run(lines=1, reviews=[("AKA", "UPDATE_REQUIRED", 0)]))
    row = quote_rows(run)[0]
    a3 = QUOTE_HEADERS.index("A3 Evaluate Inventory, BOM & AKA")
    assert row[a3:a3 + 3] == ["Fail", "Not run", "Not run"]


def test_stage_after_a3_failure_shows_its_result_when_it_did_run():
    run = evaluate(_run(lines=1, reviews=[("AKA", "UPDATE_REQUIRED", 0), ("PRICING", "NO_ACTION", 0)]))
    row = quote_rows(run)[0]
    a3 = QUOTE_HEADERS.index("A3 Evaluate Inventory, BOM & AKA")
    assert row[a3:a3 + 3] == ["Fail", "Pass", "Not run"]


def test_field_names_are_normalized():
    from app.modules.reporting.review_workbook import _field_label

    assert _field_label("customer_part_number", None) == "Customer Part Number"
    assert _field_label(None, "AKA customer part number") == "Customer Part Number"
    assert _field_label("rev", None) == _field_label(None, "AKA revision") == "Revision"
    assert _field_label("part_description", None) == _field_label(None, "AKA description") == "Part Description"
    assert _field_label("customer", None) == "Customer"
    assert _field_label("None", "AKA selling multiple") == "Selling Multiple"
    assert _field_label("some_new_field", None) == "Some New Field"
    assert _field_label("(none)", None) == ""


def test_rules_reference_has_active_rule_status():
    from app.modules.reporting.rule_reference import RULE_REFERENCE, RULE_REFERENCE_HEADERS

    column = RULE_REFERENCE_HEADERS.index("Rule Status")
    assert all(len(entry) == len(RULE_REFERENCE_HEADERS) for entry in RULE_REFERENCE)
    assert {entry[column] for entry in RULE_REFERENCE} <= {"Active", "Inactive"}


def test_latest_run_per_quote_keeps_newest():
    older = _run(created_at=datetime(2026, 11, 1))
    newer = _run(created_at=datetime(2026, 11, 2))
    other = _run(quote_id="99999-001", created_at=datetime(2026, 11, 1))
    kept = latest_run_per_quote([older, newer, other])
    assert [run["processing_id"] for run in kept] == [newer["processing_id"], other["processing_id"]]


def test_iso_dates_in_text_become_mm_dd_yyyy():
    from app.modules.reporting.review_workbook import _clean

    assert _clean("effective 2026-07-06, comment 65150-003") == "effective 07-06-2026, comment 65150-003"
    assert _clean("65150-003") == "65150-003"


def test_price_effective_date_is_date_only():
    from app.modules.reporting.review_workbook import _date_only

    assert _date_only(datetime(2026, 7, 6, 0, 0)) == datetime(2026, 7, 6, 0, 0).date()


def test_comment_cleanup_keeps_slashes_and_removes_api_codes():
    from app.modules.reporting.client_report import _readable_comment

    assert _readable_comment("the quote has '6429/9481662'.") == "the quote has '6429/9481662'."
    assert _readable_comment("SO 302139-AMP / detail 78841: hold") == "SO 302139-AMP / detail 78841: hold"
    assert _readable_comment("Add a new price break; PRICE_BREAK_WRITE_GAP") == "Add a new price break"
