"""
Maps this dataset's internal scenario tags (used in mock_quote_data.py) back
to the official rule codes from "EVCO Quote Validation AI Agentic Rules and
Requirements" (Incede.ai, Aug 13 2026) - Business Rules (BR-xxx), Business
Requirements (BRM-xxx), Validation Rules (VR-xxx), and Exception Rules
(EX-xxx) - plus this project's own PDF-annotation conventions (ANN-xxx),
which are real, code-implemented behavior in app/modules/extraction/parser.py
but are not part of the spec's numbered rule catalog.

`verifiability` on each rule (see business_rule_plan.md for full detail):
  - "code-verifiable"   - deterministic code in this repo; checked directly by
                          validate_mock_quotes.py without needing the LLM/API.
  - "llm-dependent"     - would be produced by parser.py's LLM call
                          (parts[]/pricing_tiers[] field values); not
                          independently checkable without live API credentials.
  - "agent-deferred"    - the spec itself marks this as evaluated by a
                          downstream agent's reasoning, not by code in this
                          repository (see the spec's own "(evaluated via
                          A3/A4 agent reasoning)" annotations).
  - "unverifiable-no-fixture" - would need a backing data source (a live/seeded
                          customer or AKA list) that does not exist in this
                          project.
"""

RULE_CATALOG = {
    # --- Business Requirements (extraction) ---------------------------------
    "BRM-001": {"title": "Extract quote header information", "category": "BRM", "verifiability": "code-verifiable"},
    "BRM-002": {"title": "Extract quote line information", "category": "BRM", "verifiability": "llm-dependent"},
    "BRM-004": {"title": "Create one pricing record per quantity tier", "category": "BRM", "verifiability": "llm-dependent"},
    "BRM-007": {"title": "Store price-entry date", "category": "BRM", "verifiability": "agent-deferred"},

    # --- Business Rules ------------------------------------------------------
    "BR-001": {"title": "Process only active quotes", "category": "BR", "verifiability": "code-verifiable"},
    "BR-002": {"title": "Support single pricing and tier pricing", "category": "BR", "verifiability": "llm-dependent"},
    "BR-003": {"title": "Process each manufacturing number separately", "category": "BR", "verifiability": "llm-dependent"},
    "BR-008": {"title": "Continue using an active quote until it expires", "category": "BR", "verifiability": "agent-deferred"},
    "BR-009": {"title": "Earlier release uses earlier quote", "category": "BR", "verifiability": "agent-deferred"},
    "BR-010": {"title": "Later release uses new quote", "category": "BR", "verifiability": "agent-deferred"},
    "BR-012": {"title": "Use quantity tier based on qualifying ordered quantity", "category": "BR", "verifiability": "agent-deferred"},
    "BR-014": {"title": "Re-evaluate existing Sales Orders after quote changes", "category": "BR", "verifiability": "agent-deferred"},
    "BR-015": {"title": "Expire previous pricing one day before the new quote", "category": "BR", "verifiability": "agent-deferred"},
    "BR-016": {"title": "Insert new pricing instead of overwriting history", "category": "BR", "verifiability": "agent-deferred"},
    "BR-021": {"title": "Handle AKA quantity break of 1", "category": "BR", "verifiability": "agent-deferred"},

    # --- Validation Rules ------------------------------------------------------
    "VR-005": {"title": "Validate customer identity", "category": "VR", "verifiability": "code-verifiable",
               "note": "Verified directly against app/modules/customer/repository.py's real "
                       "CustomerRepository.find_candidates(), fed by the local IQMS CustomersLite export "
                       "in data/iqms_customers_lite.json instead of a live IQMS connection."},
    "VR-006": {"title": "Prefer unique customer number", "category": "VR", "verifiability": "code-verifiable",
               "note": "Same verification path as VR-005 - see validate_mock_quotes.py."},
    "VR-007": {"title": "Use one customer number in the current scope", "category": "VR", "verifiability": "agent-deferred",
               "note": "The Tier-1/multi-customer-per-AKA question this rule raises (per the spec's own "
                       "Solution Clarifications) is explicitly parked pending EVCO/QA, not implemented."},
    "VR-009": {"title": "Validate manufacturing number/BOM", "category": "VR", "verifiability": "agent-deferred"},
    "VR-010": {"title": "Select the matching BOM when multiple BOMs exist", "category": "VR", "verifiability": "agent-deferred"},
    "VR-011": {"title": "Manufacturing number must substantially match", "category": "VR", "verifiability": "agent-deferred"},
    "VR-012": {"title": "Map box quantity to selling multiple", "category": "VR", "verifiability": "agent-deferred"},
    "VR-013": {"title": "Map MOQ to minimum sell quantity", "category": "VR", "verifiability": "agent-deferred"},

    # --- Exception Rules ------------------------------------------------------
    "EX-001": {"title": "Quote is not active", "category": "EX", "verifiability": "code-verifiable"},
    "EX-002": {"title": "Required quote header information is missing", "category": "EX", "verifiability": "code-verifiable"},
    "EX-003": {"title": "Required quote-line information is missing", "category": "EX",
               "verifiability": "code-verifiable",
               "note": "No separate EX-003 check exists in parser.py - column-header failures are reported "
                       "as EX-002 too (see the ExceptionCode class's own comment)."},
    "EX-006": {"title": "Customer is ambiguous", "category": "EX", "verifiability": "code-verifiable",
               "note": "Verified: searching the fixture by name alone for PDF3's customer ('Align "
                       "Technology') returns 2 candidates (AMBIGUOUS) because a second real IQMS record "
                       "('ALIGN TECHNOLOGY INC', used on PDF2) also starts-with/matches it at >=80% "
                       "similarity - a real production data-quality issue, not a fabricated scenario. "
                       "See validate_mock_quotes.py."},
    "EX-007": {"title": "Manufacturing number or BOM does not match", "category": "EX", "verifiability": "agent-deferred"},
    "EX-008": {"title": "Manufacturing number formatting differs", "category": "EX", "verifiability": "agent-deferred"},
    "EX-018": {"title": "Box Quantity Not Divisible by MOQ - Warning, Not Hard Stop", "category": "EX", "verifiability": "agent-deferred"},

    # --- PDF redline/annotation conventions (code-implemented, not numbered in the spec) ---
    "ANN-STRIKE": {"title": "Row struck by a red/pink line - excluded entirely from extraction",
                   "category": "ANN", "verifiability": "code-verifiable",
                   "note": "app/modules/extraction/parser.py get_page_annotations()/build_annotated_page_text() - "
                           "not one of the spec's numbered EX-xxx rules."},
    "ANN-OVERRIDE": {"title": "Small red-outlined box overrides the cell value beneath it",
                     "category": "ANN", "verifiability": "code-verifiable",
                     "note": "parser.py get_cell_overrides() - not a numbered spec rule."},
    "ANN-EFFECTIVE-DATE-OVERRIDE": {"title": "Red 'Effective to <date>' text overrides the printed Price Effective Date",
                                    "category": "ANN", "verifiability": "code-verifiable",
                                    "note": "parser.py find_effective_date_override() - not a numbered spec rule."},
}

# Row-level scenario tag -> rule code(s). A tag not present here maps to []
# (structural/extraction-robustness content with no corresponding numbered
# business rule - see each tag's row-level `notes` field for what it tests).
SCENARIO_RULE_MAP = {
    "VALID_BASELINE": ["BRM-002"],
    "MULTI_TIER_PRICING": ["BR-002", "BRM-004"],
    "SINGLE_TIER_PRICING": ["BR-002"],
    "BOM_RESIN_VARIANT": ["BR-003", "VR-009", "VR-010"],
    "BOM_COMBINED_PLUS": ["BR-003", "VR-009"],
    "BOM_SUFFIX_VARIANT": ["BR-003", "VR-009", "VR-011"],
    "CARRY_FORWARD_MERGED_CELL": ["BRM-002"],
    "MOQ_LOW_BOUNDARY": ["VR-013", "BR-012"],
    "MOQ_EQUALS_BOX_QTY": ["VR-012", "VR-013"],
    "MOQ_HIGH_VOLUME": ["VR-013", "BR-012"],
    "BOX_QTY_MOQ_NOT_DIVISIBLE": ["EX-018"],
    "AKA_QTY_BREAK_OF_ONE": ["BR-021"],
    "BLANK_CUSTOMER_PN": ["EX-003"],
    "BLANK_DESCRIPTION": ["EX-003"],
    "BLANK_MOQ_SINGLE_ROW": ["EX-003"],
    "BLANK_PRICE_SINGLE_ROW": ["EX-003"],
    "BLANK_MOLD_SINGLE_ROW": ["EX-003"],
    "BLANK_PARTS_BOX_SINGLE_ROW": ["EX-003"],
    "LONG_DESCRIPTION_WRAP": ["BRM-002"],
    "LEAD_TIME_EXTENDED": [],
    "COMBINED_MULTI_RULE": ["BR-002", "BR-003", "BR-012", "VR-013"],
    "SPECIAL_CHAR_PART_NUMBER": [],
    "DECOY_QUANTITY_COLUMN_ROW": [],
    "RESIN_MATERIAL_ROW": [],
    "PRICE_INCREASE_HIGHLIGHT": [],
    "PRICE_UNCHANGED_ROW": [],
    "STRUCK_ROW_RED_ANNOTATION": ["ANN-STRIKE"],
    "STRUCK_ROW_PINK_ANNOTATION": ["ANN-STRIKE"],
    "CELL_OVERRIDE_RED_BOX": ["ANN-OVERRIDE"],
}

# Notes on tags with an empty rule list, so the JSON isn't just silently blank:
SCENARIO_NO_RULE_REASON = {
    "LEAD_TIME_EXTENDED": "Lead time is a data field with no dedicated BR/VR/EX rule code in the spec.",
    "SPECIAL_CHAR_PART_NUMBER": "Extraction-robustness content (non-numeric part-number formatting); no dedicated rule code.",
    "DECOY_QUANTITY_COLUMN_ROW": "Exercises parser.py's prompt guidance to not confuse an adjacent quantity "
        "column (e.g. Annual Volume) with the true MOQ column - prompt-engineering guidance, not a numbered rule.",
    "RESIN_MATERIAL_ROW": "Resin/material reference rows are explicitly excluded from parts[] extraction by "
        "parser.py's own prompt instructions - no Part Pricing BR/VR/EX rule applies to them.",
    "PRICE_INCREASE_HIGHLIGHT": "Resin-table-only content; see RESIN_MATERIAL_ROW.",
    "PRICE_UNCHANGED_ROW": "Resin-table-only content; see RESIN_MATERIAL_ROW.",
}


def rules_for_scenarios(scenario_tags):
    """Unique, catalog-ordered list of rule codes for a row's scenario tags."""
    seen = []
    for tag in scenario_tags:
        for code in SCENARIO_RULE_MAP.get(tag, []):
            if code not in seen:
                seen.append(code)
    return seen


def quote_level_rules(quote):
    """Rule codes that apply to the quote as a whole (Type/header-level, not per-row)."""
    codes = []

    def add(code):
        if code not in codes:
            codes.append(code)

    if quote["type"].lower() == "active":
        add("BR-001")
    else:
        add("EX-001")

    add("BRM-001")

    missing_header = not quote["price_effective_date"]
    missing_column = bool(quote.get("missing_required_column"))
    if missing_header or missing_column:
        add("EX-002")
    if missing_column:
        add("EX-003")

    if quote.get("effective_date_override"):
        add("ANN-EFFECTIVE-DATE-OVERRIDE")

    if quote.get("supersedes_prior_quote"):
        add("BR-008")
        add("BR-009")
        add("BR-010")
        add("BR-014")
        add("BR-015")
        add("BR-016")

    if quote.get("customer_similarity_test"):
        add("VR-005")
        add("VR-006")
        add("VR-007")
        add("EX-006")

    return codes
