# Business Rule Plan (pre-generation analysis)

This is the inspection this mock dataset was designed against. It was written
by reading the actual project code before any PDF was generated - not the
"EVCO Quote Validation AI Agentic Rules and Requirements" spec document in
isolation. Where the spec and the code disagree, the code is called out
explicitly, per the "use the actual implementation as source of truth" ground
rule for this task.

## 1. What is actually implemented as code in this repository, today

| Area | Where | What it does |
|---|---|---|
| PDF extraction (header + line items) | `app/modules/extraction/parser.py` | PyMuPDF text/table extraction + an LLM call (IBM/OpenAI-compatible endpoint) for `parts[]`/`pricing_tiers[]`. |
| EX-001 (quote not Active) | `parser.validate_before_extraction`, `validate_required_fields` | Deterministic regex check on `Type:` - **no LLM call needed**. |
| EX-002 (missing header/column) | same | Deterministic: checks `quote_number`/`type`/`price_effective_date`/`customer_name` presence, and required table columns (Mold, EVCO PN, EVCO MFG/BOM, Customer PN, Description, Box Qty, MOQ, Price) via configurable aliases (`app/core/config.py` `EXTRACTION_ALIAS_*`). Also flags a column blank on a **majority** (not just all) of parts as a likely partial-extraction failure, not just a fully-absent column. |
| Color-annotation handling (redline rules) | `parser.get_page_annotations`, `build_annotated_page_text`, `get_cell_overrides`, `find_effective_date_override` | Fully deterministic vector-graphics analysis, run **before** the LLM call: red/pink strike-through rows are excluded, small red-outlined boxes override a cell's value, red "Effective to \<date\>" text overrides the printed Price Effective Date. |
| Garbled/wrapped header repair, master-header carry-over across pages, decoy-quantity-column guidance, orphaned-last-row handling | `parser.py` (`_table_header_is_garbled`, `find_master_table_header`, the LLM prompt's own column-disambiguation rules) | Structural robustness against real-world PDF quirks observed in the reference documents. |
| Customer resolution (VR-005/006/007, EX-005/006) | `app/modules/customer/repository.py`, `app/modules/customer/service.py` | Exact customer-number match, then name starts-with + ≥80% `SequenceMatcher` similarity; ambiguous (EX-006) / mismatch (EX-005) handling. **Reads its candidate list live from IQMS** (`IQMSClient.get_customers_lite()`) - there is no local customer fixture/JSON anywhere in this repository. |
| Inventory / BOM / AKA / Pricing "tools" (T4-T11) | `app/modules/inventory/*`, `app/modules/pricing/service.py` | Thin FastAPI wrappers. `get-aka` is now IQMS-live; search-part/get-bom-candidates/create-aka/update-aka/price-breaks are an **in-memory mock store with dynamic fallback** - any EVCO part number, BOM, or customer/item key not explicitly seeded still returns a plausible synthesized record. No MOQ/quantity-tier/BOM-matching *decision* logic exists in this code - it just returns data. |
| Sales Order lookups (T12-T16) | `app/modules/sales_order/service.py` | Thin FastAPI wrappers around live IQMS Sales Order endpoints. No date/tier/compliance decision logic here either. |

## 2. What the spec itself says is NOT code in this repository

The spec document ("EVCO Quote Validation AI Agentic Rules and Requirements",
Aug 13 2026) repeatedly annotates rules with "(evaluated via A3/A4 agent
reasoning)" - e.g. BR-002/BR-008/BR-012 (tier selection), VR-009/VR-012
through VR-019 (BOM/MOQ/price/date matching), most of BR-007 through BR-022
(Sales Order timing/effective-date logic). These are intended to be judgment
calls made by an LLM-driven agent that consumes this backend's tool endpoints
- **that agent is not part of this codebase**. No amount of PDF content can
make this repository's current code "pass" or "fail" those rules, because
nothing in the repository evaluates them yet.

**Consequence for this dataset:** every row is realistic, internally
consistent input *for* that future agent (varied MOQs, tiers, BOM formats,
dates, lead times), but this dataset's `expected_result` values only assert
outcomes for the rules that are genuinely code-verifiable today (see
`business_rule_coverage.md`'s verifiability column). Anything else is labeled
`structural-analog` or `agent-deferred` rather than asserted as pass/fail -
per this task's own instruction not to invent expected results the project
can't yet produce.

## 3. Customer matching: no backing data source exists

`CustomerRepository.get_all_records()` calls `IQMSClient.get_customers_lite()`
over live HTTP - there is no local JSON/CSV/fixture of EVCO customers
anywhere in this repo, and the task's own instructions to "use the actual
customer JSON" assumed one exists. It doesn't. Given the stated constraint
that a live IQMS connection isn't available for this testing effort, VR-005
(customer match), VR-006 (prefer customer number), VR-007 (one customer per
AKA) and EX-005/EX-006 cannot be exercised end-to-end here.

What this dataset does instead: PDF2 ("Northwind Components Co.") and PDF3
("Northwind Components Company") deliberately use near-identical customer
names differing only by a legal-suffix variant - exactly the shape
`CustomerRepository._normalize_customer_name` + `SequenceMatcher` similarity
(≥80% threshold) is built to resolve. If/when a real or seeded customer list
becomes available, these two PDFs are ready-made fixtures for that test;
until then this is documented as a **designed-but-unverified** scenario, not
a false claim of end-to-end coverage.

## 4. Table structure caveat worth flagging (found during inspection)

`parser.py`'s prompt instructs the LLM to merge two consecutive rows into one
`parts[]` entry (with multiple `pricing_tiers`) whenever they share the same
EVCO part number, mold number, **and box quantity** - it does not check BOM
number as a merge key. The real reference quote `65150-003.pdf` (and this
dataset's `BOM_RESIN_VARIANT` rows, which follow the same shape) has two rows
per part sharing EVCO PN/Mold/Box Qty but differing only by resin/BOM suffix
(e.g. `-CHIMEI` vs `-SABIC`). Per the prompt's literal merge rule, the LLM may
end up merging these into one part with all tiers from both BOM variants
lumped together, rather than keeping them as two parts. This is a pre-existing
ambiguity in the real extraction prompt, not something a mock PDF can control
- flagged here rather than silently assumed away.

## 5. Plan actually executed

- 5 PDFs, `_DUMMY.pdf` suffix, visually modeled on the 3 real reference
  templates (`65150-003.pdf` tiered/resin style, `58312-035A MOCK PROD
  QUOTE.pdf` wide master-list style, `64399-01x Billy Bob.pdf` simple/decoy-
  column style) - see `README.md` for the file-to-template mapping.
- 301 total Part Pricing table rows (56-65 per PDF), each tagged with one or
  more scenario codes in `mock_quote_row_manifest.json` / `data/mock_quote_data.json`.
- 2 of the 5 PDFs are quote-level EX-001/EX-002 exceptions (necessarily
  whole-document, since `Type:` and the header fields are document-level, not
  row-level) - both still carry full 50+ row tables underneath so they aren't
  "thin" documents, and both are verified to trigger the *intended* failure
  and no other, using the project's real `validate_before_extraction()`.
- Every red/pink strike-row and cell-override-box annotation, and the one
  "Effective to" date-override annotation, were verified against the real
  `get_page_annotations()` / `find_effective_date_override()` functions - see
  `validate_mock_quotes.py` output.
- No customer JSON fixture was fabricated to satisfy the "use the customer
  JSON" instruction, since none exists in this project; see Section 3.
