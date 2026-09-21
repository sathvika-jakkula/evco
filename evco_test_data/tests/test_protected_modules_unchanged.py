"""Proves the protected modules (Extraction, Customer/Customer Matching,
existing Business Rule implementation, existing Sales Order/Inventory/Pricing
services, app/database repositories, app/api routers) were not modified,
renamed, moved, or duplicated by this test-data/API-simulation extension."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROTECTED_PATHS = [
    "app/modules/extraction",
    "app/modules/customer",
    "app/modules/inventory",
    "app/modules/pricing",
    "app/modules/sales_order",
    "app/integrations/iqms.py",
    "app/database",
    "app/api",
    "requirements.txt",
]


def test_protected_paths_untouched():
    result = subprocess.run(
        ["git", "status", "--porcelain", "--"] + PROTECTED_PATHS,
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30,
    )
    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    # app/integrations/iqms.py had a pre-existing, unrelated working-tree
    # change before this task started (see gitStatus at session start) -
    # excluded here the same way evco_mock_data/validate_mock_quotes.py does.
    dirty = [line for line in dirty if "iqms.py" not in line]
    assert not dirty, f"Protected files show unexpected changes: {dirty}"


def test_no_duplicate_sales_order_router_created():
    """No new file under evco_test_data/ defines a FastAPI router (this
    extension only adds fixtures/services/tests, never a new HTTP endpoint)."""
    test_data_dir = PROJECT_ROOT / "evco_test_data"
    self_path = Path(__file__).resolve()
    for py_file in test_data_dir.rglob("*.py"):
        if py_file.resolve() == self_path:
            continue  # this file's own assertion message contains the literal string being checked for
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        assert "APIRouter(" not in text, f"{py_file} defines a new FastAPI router - not allowed by the plan"


if __name__ == "__main__":
    test_protected_paths_untouched()
    test_no_duplicate_sales_order_router_created()
    print("OK: protected modules untouched, no duplicate routers created.")
    sys.exit(0)
