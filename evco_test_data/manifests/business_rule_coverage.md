# Business Rule Coverage — evco_test_data

Rule codes exercised across the 5 reused PDFs (`evco_mock_data/pdfs/`), by row/quote count.
Source of truth: `evco_mock_data/data/rule_catalog.py` (not duplicated here).

| Rule Code | Category | Title | Verifiability | Rows/Quotes exercising it |
|---|---|---|---|---|
| ANN-EFFECTIVE-DATE-OVERRIDE | ANN | Red 'Effective to <date>' text overrides the printed Price Effective Date | code-verifiable | 1 |
| ANN-OVERRIDE | ANN | Small red-outlined box overrides the cell value beneath it | code-verifiable | 2 |
| ANN-STRIKE | ANN | Row struck by a red/pink line - excluded entirely from extraction | code-verifiable | 4 |
| BR-001 | BR | Process only active quotes | code-verifiable | 4 |
| BR-002 | BR | Support single pricing and tier pricing | llm-dependent | 116 |
| BR-003 | BR | Process each manufacturing number separately | llm-dependent | 105 |
| BR-008 | BR | Continue using an active quote until it expires | agent-deferred | 3 |
| BR-009 | BR | Earlier release uses earlier quote | agent-deferred | 3 |
| BR-010 | BR | Later release uses new quote | agent-deferred | 3 |
| BR-012 | BR | Use quantity tier based on qualifying ordered quantity | agent-deferred | 48 |
| BR-014 | BR | Re-evaluate existing Sales Orders after quote changes | agent-deferred | 3 |
| BR-015 | BR | Expire previous pricing one day before the new quote | agent-deferred | 3 |
| BR-016 | BR | Insert new pricing instead of overwriting history | agent-deferred | 3 |
| BR-021 | BR | Handle AKA quantity break of 1 | agent-deferred | 2 |
| BRM-001 | BRM | Extract quote header information | code-verifiable | 5 |
| BRM-002 | BRM | Extract quote line information | llm-dependent | 186 |
| BRM-004 | BRM | Create one pricing record per quantity tier | llm-dependent | 44 |
| BRM-007 | BRM | Store price-entry date | agent-deferred | 0 |
| EX-001 | EX | Quote is not active | code-verifiable | 1 |
| EX-002 | EX | Required quote header information is missing | code-verifiable | 1 |
| EX-003 | EX | Required quote-line information is missing | code-verifiable | 9 |
| EX-006 | EX | Customer is ambiguous | code-verifiable | 2 |
| EX-007 | EX | Manufacturing number or BOM does not match | agent-deferred | 0 |
| EX-008 | EX | Manufacturing number formatting differs | agent-deferred | 0 |
| EX-018 | EX | Box Quantity Not Divisible by MOQ - Warning, Not Hard Stop | agent-deferred | 11 |
| VR-005 | VR | Validate customer identity | code-verifiable | 2 |
| VR-006 | VR | Prefer unique customer number | code-verifiable | 2 |
| VR-007 | VR | Use one customer number in the current scope | agent-deferred | 2 |
| VR-009 | VR | Validate manufacturing number/BOM | agent-deferred | 100 |
| VR-010 | VR | Select the matching BOM when multiple BOMs exist | agent-deferred | 45 |
| VR-011 | VR | Manufacturing number must substantially match | agent-deferred | 38 |
| VR-012 | VR | Map box quantity to selling multiple | agent-deferred | 1 |
| VR-013 | VR | Map MOQ to minimum sell quantity | agent-deferred | 49 |

**Not exercised by any row/quote in this dataset:** BRM-007, EX-007, EX-008