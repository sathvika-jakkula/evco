# EVCO Mock Quote Test Data

Fictional EVCO Price Quotation PDFs for testing the Inventory/Pricing/
Extraction tooling in this repository, built because no live IQMS/EVCO API
connection is available for end-to-end testing right now.

**This folder is fully isolated from the real project.** Nothing under
`app/`, `requirements.txt`, `openapi/`, or `scripts/` was modified to build
or run this - see `business_rule_plan.md` §5 and the git-integrity check at
the end of `validate_mock_quotes.py`.

## What's here

```
evco_mock_data/
  README.md                      - this file
  business_rule_plan.md          - pre-generation code inspection: what's actually
                                    implemented vs. deferred to agent reasoning,
                                    and known limitations (read this first)
  business_rule_coverage.md      - generated: every row, its scenario tag(s),
                                    verifiability, and expected result
  mock_quote_manifest.json       - generated: one entry per PDF (quote-level metadata)
  mock_quote_row_manifest.json   - generated: one entry per pricing row
  generate_mock_quotes.py        - regenerates the PDFs + manifests + coverage doc
  validate_mock_quotes.py        - re-validates everything against the real
                                    project code (see below)
  data/
    mock_quote_data.py           - the actual row/quote data (source of truth -
                                    edit this, not the generated JSON)
    mock_quote_data.json         - generated dump of the same data
  pdfs/
    EVCO_QUOTE_74021_DUMMY.pdf
    EVCO_QUOTE_74022_DUMMY.pdf
    EVCO_QUOTE_74023_DUMMY.pdf
    EVCO_QUOTE_74024_DUMMY.pdf
    EVCO_QUOTE_74025_DUMMY.pdf
```

All company names, contact names, part numbers, BOMs, and prices are
fictional. Every PDF's filename ends in `_DUMMY.pdf` and every page carries a
red "MOCK DATA - NOT A REAL QUOTE" box, mirroring the "NOT A REAL QUOTE DO NOT
USE" convention already used in this project's own `58312-035A MOCK PROD
QUOTE.pdf` reference file.

## The 5 PDFs

| File | Customer | Template (modeled on) | Type | Pages | Rows | Quote-level exception |
|---|---|---|---|---|---|---|
| EVCO_QUOTE_74021_DUMMY.pdf | Acme Dynamics Corporation | tiered/resin (`65150-003.pdf`) | Active | 5 | 60 | - |
| EVCO_QUOTE_74022_DUMMY.pdf | Northwind Components Co. | wide master-list (`58312-035A MOCK PROD QUOTE.pdf`) | Active | 5 | 65 | - |
| EVCO_QUOTE_74023_DUMMY.pdf | Northwind Components Company | simple/decoy-column (`64399-01x Billy Bob.pdf`) | Active | 4 | 56 | - |
| EVCO_QUOTE_74024_DUMMY.pdf | Ironclad Fasteners LLC | tiered/resin | **Inactive** | 5 | 58 | EX-001 |
| EVCO_QUOTE_74025_DUMMY.pdf | Falcon Drivetrain Systems | wide master-list | Active | 5 | 62 | EX-002 (×2 causes) |

**301 pricing rows total**, each tagged with one or more of ~27 scenario codes
(BOM resin/family variants, multi-tier pricing, MOQ boundaries, carry-forward
merged cells, decoy quantity columns, sparse/blank fields, long-wrapping
descriptions, and 3 kinds of PDF color-annotation: a red struck-out row, a
pink struck-out row, and a red-boxed price override) - see
`business_rule_coverage.md` for the full row-by-row list and
`business_rule_plan.md` for what each tag is meant to exercise.

**PDF2 and PDF3 use deliberately near-identical customer names** ("Northwind
Components Co." vs "Northwind Components Company") to exercise
`CustomerRepository`'s name-similarity matching logic - see
`business_rule_plan.md` §3 for why this can't be verified end-to-end without a
live/seeded customer list.

**PDF4 and PDF5 are quote-level exceptions on purpose.** `Type:`/header
fields are document-level, not row-level, so EX-001 (inactive quote) and
EX-002 (missing header field / missing required column) can only be
demonstrated as whole-document failures - both still contain full 50+ row
tables with the same scenario variety as the other 3 PDFs, so the exception
is the only thing distinguishing them, not a "thin" or unrealistic document.

## Regenerating

```
cd evco_mock_data
python generate_mock_quotes.py
```

This rewrites everything under `pdfs/`, `data/mock_quote_data.json`,
`mock_quote_manifest.json`, `mock_quote_row_manifest.json`, and
`business_rule_coverage.md` from `data/mock_quote_data.py`. To change the
dataset, edit `data/mock_quote_data.py` and regenerate - don't hand-edit the
generated JSON/Markdown files, they'll just be overwritten.

Uses PyMuPDF (`fitz`), which is already a dependency of the real project's
PDF extractor - no new package was added to `requirements.txt`.

## Validating

```
cd evco_mock_data
python validate_mock_quotes.py
```

This is the important one. Rather than just trusting the generation
metadata, it **imports `app/modules/extraction/parser.py` directly** and runs
the real, deterministic (non-LLM) functions against every generated PDF:

- `has_image_only_table()` - confirms every PDF is genuine text, not an image.
- `extract_header_fields()` - confirms quote number/type/customer/effective
  date are extracted correctly, including PDF2's red "Effective to" date
  override actually superseding the printed date.
- `find_master_table_header()` + `validate_before_extraction()` - confirms
  PDF1/2/3 produce **zero** pre-extraction failures and PDF4/PDF5 produce
  **exactly** the intended EX-001 / EX-002 codes (and no others).
- `get_page_annotations()` - confirms every red/pink strike-row and
  red-boxed price override actually gets detected as such by the real
  color-classification logic, at the exact counts this dataset intends.
- Cross-checks row counts, filenames, and page counts against the manifests.
- Runs `git status` on `app/`, `requirements.txt`, `openapi/`, `scripts/` to
  confirm nothing in the real project was touched.

**What it cannot validate:** the LLM-based `parts[]`/`pricing_tiers[]`
extraction itself (`parser.call_llm`, via the IBM/OpenAI-compatible API) -
that needs live `IBM_API_KEY`/`IBM_BASE_URL` credentials this environment
doesn't have. Everything the validator checks runs *before* that call and is
plain deterministic Python, so a clean run here is a genuine, code-grounded
guarantee for that half of the pipeline - not a claim about the LLM's own
field-mapping fidelity, which can only be confirmed by actually running
extraction end-to-end once credentials are available.

## Known limitations (see business_rule_plan.md for detail)

1. No local customer/AKA/pricing fixture exists in this project - Inventory
   and Pricing "tools" are in-memory mocks with dynamic fallback (any part
   number works), and Customer/Sales-Order lookups hit live IQMS with no
   local substitute. Most MOQ-tier-selection, BOM-matching, and date-logic
   *business rules* are explicitly deferred to future agent reasoning per the
   spec itself, not implemented as code here - this dataset gives that future
   agent realistic, varied input, but can't assert its output.
2. `parser.py`'s same-part merge rule (EVCO PN + Mold + Box Qty, not BOM) may
   merge this dataset's BOM-resin-variant row pairs into one `parts[]` entry
   during real LLM extraction - a pre-existing ambiguity in the real
   extraction prompt, not a defect in this test data.
