"""Requires a real Postgres reachable via the project's own DATABASE_URL
(app/core/config.py) with evco_test_data/db/schema_bootstrap.sql applied.
Proves the new pricing_history table preserves history instead of
overwriting it (BR-015/BR-016) when driven through the additive hook, which
itself calls the real, unmodified PriceBreakService."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database.connection import DatabaseConnection  # noqa: E402
from app.modules.pricing.schemas import UpdatePriceBreakRequest  # noqa: E402
from app.modules.pricing.service import PriceBreakService  # noqa: E402
from evco_test_data.db.pricing_history_repository import PricingHistoryRepository  # noqa: E402
from evco_test_data.mock_api.pricing_history_hook import record_price_update_with_history  # noqa: E402


def db_reachable() -> bool:
    try:
        with DatabaseConnection().connect():
            return True
    except Exception:
        return False


def test_price_change_preserves_history():
    if not db_reachable():
        print("SKIPPED: DATABASE_URL not reachable in this environment.")
        return

    repo = PricingHistoryRepository()
    pricing = PriceBreakService()

    record_price_update_with_history(
        pricing, repo,
        UpdatePriceBreakRequest(quantity=200, price=62.65, effective_date="2026-07-20T00:00:00"),
        customer_number="10329", evco_part_number="9480026", manufacturing_bom_number="6601/9480026-CHIMEI",
        source_quote_number="74021 - 001",
    )
    first = repo.get_active_price("10329", "9480026", "6601/9480026-CHIMEI", 200)
    assert first is not None and float(first["price"]) == 62.65

    record_price_update_with_history(
        pricing, repo,
        UpdatePriceBreakRequest(quantity=200, price=70.00, effective_date="2026-09-01T00:00:00"),
        customer_number="10329", evco_part_number="9480026", manufacturing_bom_number="6601/9480026-CHIMEI",
        source_quote_number="74021 - 002",
    )

    history = repo.get_history("10329", "9480026", "6601/9480026-CHIMEI", 200)
    active = [h for h in history if h["is_active"]]
    inactive = [h for h in history if not h["is_active"]]
    assert len(active) == 1 and float(active[0]["price"]) == 70.00, "exactly one active row, at the new price"
    assert len(inactive) >= 1 and any(float(h["price"]) == 62.65 for h in inactive), (
        "the $62.65 version must still exist, marked inactive - not overwritten"
    )
    print("OK: pricing_history preserves the previous version instead of overwriting it.")


if __name__ == "__main__":
    test_price_change_preserves_history()
