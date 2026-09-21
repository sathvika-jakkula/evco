"""Exercises the REAL CustomerRepository.find_candidates() (unmodified) against
FixtureIQMSClient instead of a live IQMS connection - proves VR-005/VR-006/EX-006
against the real code, not a re-implementation. Mirrors the pattern already
proven in evco_mock_data/validate_mock_quotes.py."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.modules.customer.repository import CustomerRepository  # noqa: E402
from evco_test_data.mock_api.fixture_iqms_client import FixtureIQMSClient  # noqa: E402


def _repo():
    return CustomerRepository(iqms_client=FixtureIQMSClient())


def test_unique_customer_match():
    candidates = _repo().find_candidates(customer_name="ALIGN TECHNOLOGY INC", customer_number=None)
    assert len(candidates) == 1
    assert candidates[0].customer_number == "11965"


def test_ambiguous_customer_match_by_name_only():
    candidates = _repo().find_candidates(customer_name="Align Technology", customer_number=None)
    assert len(candidates) == 2  # real duplicate IQMS record: EX-006 territory


def test_ambiguous_resolved_by_customer_number():
    candidates = _repo().find_candidates(customer_name="Align Technology", customer_number="DH102")
    assert len(candidates) == 1
    assert candidates[0].customer_number == "DH102"


def test_remaining_quote_customers_resolve_uniquely():
    for name, number in [
        ("CLACK CORPORATION", "10329"),
        ("GREENHECK FAN CORP", "11853"),
        ("HARLEY-DAVIDSON MOTOR CO", "11967"),
    ]:
        candidates = _repo().find_candidates(customer_name=name, customer_number=None)
        assert len(candidates) == 1, f"{name}: expected unique match, got {candidates}"
        assert candidates[0].customer_number == number


if __name__ == "__main__":
    test_unique_customer_match()
    test_ambiguous_customer_match_by_name_only()
    test_ambiguous_resolved_by_customer_number()
    test_remaining_quote_customers_resolve_uniquely()
    print("OK: customer matching against FixtureIQMSClient behaves as expected.")
