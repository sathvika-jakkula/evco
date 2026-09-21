## API ↔ Database Reference — Full Field Detail

## The new-vs-update rule, stated once

State tables (aka_records, pricing_history, sales_orders_synced) are looked up by business key. If a matching row exists, you update it in place (or, for pricing_history only, close it and open a new version). You never insert a second row for the same business key. Result tables (*_results, line_item_audit, customer_resolution_results) are looked up by line_item_id (or processing_id for customer resolution/quote_audit). Same rule: update in place — INSERT ... ON CONFLICT (key) DO UPDATE. A retry must never create a second result row. The one deliberate exception is pricing_history. A price change closes the old row (is_active=false) and inserts a new one (is_active=true) in the same transaction — 2 rows, by design, for audit history. exception_logs and sent_notifications are append-only event logs. Every call is a new row on purpose

— a retry legitimately gets a second row.

**Second deliberate exception (confirmed 2026-09-18):** `aka_resolution_results`, `pricing_results`, and `sales_order_results` are also append-only, not upsert-by-`line_item_id` as this section otherwise implies — no unique constraint exists on `line_item_id` in any of the three, and every AKA/pricing/sales-order lookup or mutation call adds a new row. This is intentional: the per-call history (what was true at each point in time for a line) is worth more than collapsing to a single current-state row, and `get_latest_for_line_item`/`get_all_for_line_item` (used by `/reporting/quote-summary`) already treat these tables as logs, picking the most recent row by `created_at`. Do not "fix" this into an upsert without re-checking that reporting dependency first.

## GET /health

No tables touched.

## POST /customers/resolve

Request: processing_id, customer_name Response: match_status, candidates[], customer_resolution_id, exception_codes[], exception_ids[] customer_resolution_results (upsert, key = processing_id)

Column

customer_resolution_id

processing_id

line_item_id

customer_name

customer_no

resolution_status

matched_customer

candidate_customers

status

created_at

quote_audit (update, key = processing_id)

Column

customer_name

Value

new PK

request

null / optional

request customer_name

response candidates[].customer_number (when UNIQUE)

RESOLVED / NEEDS_REVIEW / NOT_FOUND

response candidate name

response candidates[] (jsonb)

SUCCESS / PENDING / FAILED

system

Value

request customer_name


customer_no

response matched customer number

status

REVIEW_REQUIRED if MULTIPLE/NONE, else unchanged

exception_count

+1 if MULTIPLE/NONE

exception_logs (new row, only if MULTIPLE or NONE)

## Column Value

exception_id

processing_id

exception_code

is_retryable

resolved

created_at

new PK

request

CUSTOMER_AMBIGUOUS / CUSTOMER_NOT_FOUND

false

false

system

## POST /api/extract-quote

## Request: file_name, folder_path, scan_id, header callbackUrl Response: 202 immediately; full payload delivered later to callbackUrl quote_processing (new row)

## Column Value

processing_id

quote_id

filename

received_time

processing_status

current_agent

retry_count

raw_extracted_json

normalized_json

completed_at

processing_duration_ms

new PK

parsed from extraction

request file_name

system

IN_PROGRESS

first agent name

0

raw extraction payload

normalized payload

null until done

null until done


created_at

system

scan_file_id

quote_line_items (new row per part)

request scan_id → resolved scan_file_id

## Column Value

line_item_id

processing_id

quote_id / quote_part_id / line_number

evco_part_id / customer_part_number / description

bom_id / mold_number / moq / price / parts

status

created_at

quote_audit (new row)

new PK

FK to row above

parsed

parsed

parsed

EXTRACTED

system

## Column Value

quote_audit_id

processing_id

quote_id / filename / received_time

total_lines

successful_lines / review_required_lines / failed_lines / exception_count

status

completed_time / report_id

api_audit_logs (new row) — full call/response captured exception_logs (new row, only on extraction

failure) — exception_code='EXTRACTION_FAILED', line_item_id null (no lines exist yet)

new PK

FK

same as quote_processing

count of quote_line_items just created

0

IN_PROGRESS

null until done

## POST /api/processing-result

Read-only. No tables written.

## POST /inventory/get-aka

Request: Item #, customer#, mfg#, line_item_id Response: Header, akaDetails[] aka_resolution_results (upsert, key = line_item_id)


## Column Value

aka_result_id

line_item_id

aka_id

ar_invt_id

ar_cust_id

bom_id

decision

before_value

after_value

status

created_at

aka_records — only touched if you choose to mirror/cache (optional, see earlier discussion). If mirrored: upsert on (customer_number, item_number, manufacturing_bom_number) with the response values.

exception_logs (new row, only if not found) — exception_code='AKA_NOT_FOUND'

new PK

request

Delete it

Delete it

Delete it

Delete it

FOUND / NOT_FOUND

null (this is a read, nothing changed yet)

response akaDetails (jsonb)

SUCCESS / REVIEW_REQUIRED

system

## POST /inventory/create-aka

Request: Item #, createAkaDetails{...}, line_item_id Response: 201, Header + akaDetails[]

aka_records (new row, key = (customer_number, item_number, manufacturing_bom_number))

## Column Value

aka_record_id

customer_number

customer_part_number

item_number

aka_description

item_description

uom

currency

new PK

request customer#

Add in payload

request Item #

request akaDescription

Delete

request currency

not in payload — null unless carried from a prior get-aka


manufacturing_bom_number request mfg#

moq

request minimumSellingQty

selling_multiples_of

request sellingMultiplesOf

rev

request rev

not in payload — copy from customer_resolution_results.matched_customer

customer_name

ship_to_attn

request shipToAttn

status

CREATED

created_at / updated_at

system

aka_resolution_results (upsert, key = line_item_id) — decision='CREATED', after_value = new record

exception_logs (new row, only on unique-constraint race) — exception_code='AKA_CREATE_RACE'

## POST /inventory/update-aka

Request: Item #, customer#, mfg#, updateAkaDetails{...}, line_item_id Response: 200, updated Header + akaDetails[] aka_records (update in place, same row, matched by business key)

Column Customer Pn

moq

selling_multiples_of

ship_to_attn

aka_description / rev / currency

updated_at

(all other columns unchanged)

aka_resolution_results (upsert, key = line_item_id) — decision='UPDATED', before_value = prior state, after_value = new state exception_logs (new row, only if target row missing) —

exception_code='AKA_UPDATE_TARGET_MISSING'

Value Add in request

request minimumSellingQty

request sellingMultiplesOf

request shipToAttn

request, if present

system

—

## POST /inventory/get-pricebreaks

Request: evco_part_number, customer_number, manufacturing_bom_number, line_item_id Response: [{ unit_price, quantity, comment }] pricing_results (upsert, key = line_item_id)


Column

pricing_result_id

line_item_id

pricing_action

pricing_before_value / pricing_after_value

inactivated_price_rows

effective_pricing

decision

status

created_at

Value

new PK

request

READ

null (read only)

null

response array (jsonb)

FOUND / NOT_FOUND

SUCCESS / REVIEW_REQUIRED

system

exception_logs (new row, only if nothing found at requested quantity) —

exception_code='PRICE_NOT_FOUND'

## POST /inventory/add-pricebreak

Request: quantity, price, effective_date, evco_part_number, customer_number, manufacturing_bom_number, currency, source_quote_number, processing_id, line_item_id

pricing_history (new row, key = business key)

Column

pricing_history_id

evco_part_number / customer_number / manufacturing_bom_number / quantity request / price / currency / effective_date / source_quote_number

inactive_date

is_active

source_processing_id

created_by_line_item_id

inactivated_by_line_item_id

created_at / updated_at

pricing_results (upsert, key = line_item_id) — pricing_action='CREATED', pricing_after_value=request

price

Value

new PK

null

true

request processing_id

request line_item_id

null

system


exception_logs (new row, only on unique-constraint race) — exception_code='PRICE_CREATE_RACE'

## POST /inventory/update-pricebreak

Request: quantity, price, effective_date, inactive_date, evco_part_number, customer_number, manufacturing_bom_number, currency, source_quote_number, processing_id, line_item_id pricing_history — two rows written in one transaction:

Old row (update):

Column

is_active

inactive_date

inactivated_by_line_item_id

updated_at

New row (insert): same shape as add-pricebreak above, created_by_line_item_id = request line_item_id pricing_results (upsert, key = line_item_id) — pricing_action='UPDATED', pricing_before_value, pricing_after_value exception_logs (new row, only if no active row to close) —

exception_code='PRICE_UPDATE_TARGET_MISSING'

Value

false

From payload

request line_item_id

system

## POST /monitoring/scan

Request: folder_path Response: scan_id, scan_timestamp, files_detected[], file_count quote_scans (new row)

Column

scan_id

scan_status

total_files_found

successful_files / failed_files

scan_started_at / scan_completed_at / created_at

quote_scan_files (new row per file, unique on (scan_id, filename))

Column

scan_file_id

scan_id

filename

Value

new PK

COMPLETED / FAILED

response file_count

derived

Value

new PK

FK

response files_detected[] entry

system


SCANNED — never updated again by any endpoint

file_status

created_at

system

## POST /sales-orders/get-sales-orders

## Request: item_number, line_item_id

sales_orders_synced (upsert, key = sales_order_detail_id)

## Column Value

sales_order_id / sales_order_detail_id

ar_invt_id / order_number / po_number / customer_number / company / response item_number / description / customer_item_number / status / total_qty_ordered / unit_price / date_taken / delivery_date

defaults to 'dummy' — confirm real value before production

source

response

synced_at

system

line_item_sales_orders (new row per pairing, ON CONFLICT DO NOTHING) — line_item_id, sales_order_detail_id sales_order_results (upsert, key = line_item_id)

## Column Value

sales_order_result_id

sales_order_hold

sales_order_id

sales_order_note

notes

updates

decision / status

created_at

exception_logs (new row, only if hold triggered) — exception_code='SALES_ORDER_HOLD'

new PK

derived from business rule

copied

derived

unclear vs. sales_order_note — likely redundant

jsonb of what changed

derived

system


## POST /sales-orders/get-sales-order-details

Same sales_orders_synced fields as above, upsert by sales_order_detail_id. No result-table write of its

own — feeds into the same sales_order_results row via get-sales-orders.

## POST /sales-orders/get-sales-order-releases

sales_order_releases_synced (upsert, key = release_id — the one place an external IQMS id is safe to use

directly)

## Column Value

release_id

sales_order_detail_id

qty / must_ship_date / ship_date

synced_at

line_item_id

response (IQMS-issued)

request

response

system

request

## POST /quote-processing/record-exception

exception_logs (always new row — append-only, never upsert)

Column

exception_id

processing_id / line_item_id

agent_name / tool_name

exception_code / exception_message

retry_attempt / max_retry_count / is_retryable

resolved / resolved_by / resolved_time

created_at

quote_audit.exception_count — +1, same transaction

Value

new PK

request

request

request

request

request (defaults false/null) — no endpoint updates these later

system

## POST /quote-processing/move-quote-file

Request: filename, from_folder, to_folder, outcome Response: moved, source_path, destination_path No table is written directly by the endpoint itself — it's a filesystem move. Recommended writes around it (not automatic):

- quote_audit.status, completed_time, report_id - set once this is the last line of the quote


- quote_scan_files.file_status - currently nothing sets this to PROCESSED/MOVED; add

- api_audit_logs - log the move call itself

- exception_logs ‑ new row if moved=false

## POST /notifications/send-email

sent_notifications (always new row per recipient, never upsert)

Column

notification_id

processing_id

recipient

not in request payload — you set this yourself (e.g. EXCEPTION_ALERT)

notification_type

subject / message

delivery_status / sent_time

Value

new PK per recipient

request

one entry from to[]

request subject / body

response, per recipient

## Fields untouched by any endpoint

These columns exist in the schema but no current API call populates them. They're either dead columns, business-logic-only fields, or missing integrations — worth a deliberate decision (wire it up / drop it / confirm it's computed elsewhere) rather than leaving them silently null.

## Whole tables with no writer:

- bom_candidates ‑ no /bom/* endpoint exists anywhere in the API

- inventory_items ‐ no inventory-sync endpoint exists

## Columns within otherwise-active tables:

Table

aka_records

Column(s)

customer_part_number, item_description, customer_name

aka_records

uom

aka_resolution_results

aka_id, ar_invt_id, ar_cust_id, bom_id

exception_logs

resolved, resolved_by, resolved_time

Why

not present in create/update-aka payloads — need to be copied in from other tables by the orchestrator

no field anywhere carries this

get-aka never returns them

can be set at creation, but nothing

updates them afterward — no resolve endpoint


line_item_audit

mold_check_result, moq_check_result, rule_codes_applied

pure business-rule output, not returned by any API

quote_line_items

status

set once at creation, never advanced by any endpoint after

quote_scan_files

file_status

stuck at SCANNED, move-quote- file doesn't update it

sales_orders_synced

source

defaults to 'dummy', no endpoint sets a real value

sales_order_results

notes

unclear purpose vs. sales_order_note, likely dead/duplicate

notifications (sent_notifications)

notification_type

not in the request payload — caller-supplied, not API-supplied

Everything not listed above is touched by at least one endpoint.
