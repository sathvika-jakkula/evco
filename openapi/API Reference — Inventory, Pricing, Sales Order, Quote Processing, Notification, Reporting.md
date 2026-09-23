# API Reference — Inventory, Pricing, Sales Order, Quote Processing, Notification, Reporting

All endpoints are `POST`. Envelope is either `StandardResponse[T]` = `{ statusCode, message, data, error }` or `StandardInventoryResponse[T]` = `{ statusCode, message, data }` (noted per endpoint).

**"Payload key" below is the exact JSON key to send** — use that one. It's not a choice between two options.


Inventory, pricing, and sales-order payloads use extraction terminology. Legacy
request keys remain accepted, but responses use only the documented names.
Service attributes, database columns, and stored AKA audit snapshots keep their
existing names. The response envelope and nesting are unchanged except that
`Header` / `akaDetails` are now `header` / `aka_details`.

Pricing thresholds use `moq`; actual sales-order and shipment quantities keep
their distinct names. Extraction returns printed strings; these APIs retain
numeric quantities/prices and datetime validation. Convert printed values such
as `"2,000"` and `"$0.769"` before calling them. Inventory `part_description`
appears separately under the item header and each customer AKA mapping;
sales orders retain `customer_description` as a separate customer-specific value.

---

## `/inventory/get-aka`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `evco_part_number` | string | yes |
| `customer_number` | string | yes |
| `manufacturing_bom_number` | string | yes |
| `line_item_id` | UUID | no |

**Response `200`** — `StandardInventoryResponse[AkaSearchResponse]`:
```json
{
  "header": { "evco_part_number": "string", "rev": "string", "part_description": "string" },
  "aka_details": [
    { "customer_part_number": "string", "part_description": "string", "rev": "string", "customer_number": "string",
      "currency": "string", "customer_name": "string", "manufacturing_bom_number": "string", "ship_to_attn": "string",
      "moq": 0, "box_quantity": 0, "mold_number": "string" }
  ]
}
```

**HTTP errors:** `404 AKA_RECORD_NOT_FOUND`

**exception_logs code:** `AKA_NOT_FOUND`

**decision enum (aka_resolution_results):** `NOT_FOUND` | `NO_CHANGE`

---

## `/inventory/create-aka`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `evco_part_number` | string | yes |
| `create_aka_details` | object | yes |
| `line_item_id` | UUID | no |

`create_aka_details` object keys: `customer_part_number` (required), `part_description`, `rev`, `customer_number` (required), `currency`, `customer_name`, `manufacturing_bom_number` (required), `ship_to_attn`, `moq`, `box_quantity`, `mold_number`.

**Response `201`** — `StandardInventoryResponse[AkaSearchResponse]` (same shape as get-aka).

**Response `409`:**
```json
{ "statusCode": 409, "message": "AKA record already exists", "data": null }
```

**HTTP errors:** none (409 is a normal response body, not an exception)

**exception_logs code:** none

**decision enum:** `NO_CHANGE` (duplicate) | `CREATED`

---

## `/inventory/update-aka`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `evco_part_number` | string | yes |
| `customer_number` | string | yes |
| `manufacturing_bom_number` | string | yes |
| `update_aka_details` | object, all keys optional | yes |
| `line_item_id` | UUID | no |

`update_aka_details` object keys: `customer_part_number`, `part_description`, `rev`, `currency`, `ship_to_attn`, `moq`, `box_quantity`, `mold_number`.

**Response `200`** — `StandardInventoryResponse[AkaSearchResponse]`.

**HTTP errors:** `404 AKA_RECORD_NOT_FOUND`

**exception_logs code:** `AKA_UPDATE_TARGET_MISSING`

**decision enum:** `NOT_FOUND` | `NO_CHANGE` | `UPDATED`

---

## `/inventory/get-pricebreaks`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `evco_part_number` | string | yes |
| `customer_number` | string | yes |
| `manufacturing_bom_number` | string | yes |
| `processing_id` | UUID | no |
| `line_item_id` | UUID | no |

**Example request:**
```json
{
  "evco_part_number": "EVCO-10023",
  "customer_number": "CUST-4471",
  "manufacturing_bom_number": "MFG-8890",
  "processing_id": "8b1a9953-c461-4359-9dee-0b8a1b3c1e6f",
  "line_item_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**Response `200`** — `StandardInventoryResponse[List[PriceBreakData]]`:
```json
[ { "price": 0.0, "moq": 0, "comment": "string" } ]
```

**HTTP errors:** `404 PRICE_BREAKS_NOT_FOUND`

**exception_logs code:** `PRICE_NOT_FOUND`

**decision enum (pricing_results):** `NOT_FOUND` | `FOUND`

---

## `/inventory/add-pricebreak`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `moq` | int (>0) | yes |
| `price` | float (>0) | yes |
| `price_effective_date` | datetime | yes |
| `evco_part_number` | string | no |
| `customer_number` | string | no |
| `manufacturing_bom_number` | string | no |
| `currency` | string | no |
| `quote_number` | string | no |
| `processing_id` | UUID | no |
| `line_item_id` | UUID | no |

**Example request:**
```json
{
  "moq": 500,
  "price": 2.35,
  "price_effective_date": "2026-10-01T00:00:00Z",
  "evco_part_number": "EVCO-10023",
  "customer_number": "CUST-4471",
  "manufacturing_bom_number": "MFG-8890",
  "currency": "USD",
  "quote_number": "Q-2026-00456",
  "processing_id": "8b1a9953-c461-4359-9dee-0b8a1b3c1e6f",
  "line_item_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**Response `201`** — `StandardInventoryResponse[AddPriceBreakResponseData]`:
```json
{ "moq": 0, "price": 0.0, "price_date": "datetime", "price_effective_date": "datetime", "inactive_date": null }
```

**HTTP errors:** none

**exception_logs code:** none

**decision enum (pricing_results):** `CREATED` | `NO_CHANGE` | `UPDATED`

---

## `/inventory/update-pricebreak`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `moq` | int (>0) | yes |
| `price` | float (>0) | yes |
| `price_effective_date` | datetime | yes |
| `inactive_date` | datetime | no |
| `evco_part_number` | string | no |
| `customer_number` | string | no |
| `manufacturing_bom_number` | string | no |
| `currency` | string | no |
| `quote_number` | string | no |
| `processing_id` | UUID | no |
| `line_item_id` | UUID | no |

**Example request:**
```json
{
  "moq": 500,
  "price": 2.15,
  "price_effective_date": "2026-10-01T00:00:00Z",
  "inactive_date": "2027-01-01T00:00:00Z",
  "evco_part_number": "EVCO-10023",
  "customer_number": "CUST-4471",
  "manufacturing_bom_number": "MFG-8890",
  "currency": "USD",
  "quote_number": "Q-2026-00456",
  "processing_id": "8b1a9953-c461-4359-9dee-0b8a1b3c1e6f",
  "line_item_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

**Response `200`** — `StandardInventoryResponse[UpdatePriceBreakResponseData]`:
```json
{ "moq": 0, "price": 0.0, "price_effective_date": "datetime", "inactive_date": null }
```

**HTTP errors:** `404 PRICE_UPDATE_TARGET_MISSING`

**exception_logs code:** `PRICE_UPDATE_TARGET_MISSING`

**decision enum (pricing_results):** `NOT_FOUND` | `CREATED` | `NO_CHANGE` | `UPDATED`

---

## `/sales-orders/get-sales-orders`

**Request:**
| Field | Type | Required |
|---|---|---|
| evco_part_number | string | yes |
| line_item_id | UUID | no |

**Response `200`** — `StandardInventoryResponse[List[SalesOrderData]]`:
```json
[ { "sales_order_id": 0, "sales_order_detail_id": 0, "ar_invt_id": 0, "order_number": "string",
    "po_number": "string", "customer_number": "string", "customer_name": "string", "evco_part_number": "string",
    "part_description": "string", "customer_part_number": "string", "customer_description": "string",
    "status": "string", "rev": "string", "total_qty_ordered": 0.0, "price": 0.0,
    "date_taken": "datetime", "delivery_date": "datetime" } ]
```

**HTTP errors:** none

**exception_logs code:** none

**decision enum (sales_order_results):** `NOT_FOUND` | `NO_CHANGE` | `MISMATCH`

---

## `/sales-orders/get-sales-order-details`

**Request:**
| Field | Type | Required |
|---|---|---|
| sales_order_id | int | yes |
| ar_invt_id | int | yes |

**Response `200`** — `StandardInventoryResponse[List[SalesOrderDetailData]]`:
```json
[ { "sales_order_detail_id": 0, "sales_order_id": 0, "ar_invt_id": 0, "blanket_qty": 0.0,
    "price": 0.0, "list_unit_price": 0.0, "uom": "string", "on_hold": false, "ship_hold": false,
    "discount": 0.0, "containers": 0.0, "drop_ship": false, "po_info": "string", "note": "string" } ]
```

**HTTP errors:** none

**exception_logs code:** none

**decision enum:** none (this endpoint writes no result table)

---

## `/sales-orders/get-sales-order-releases`

**Request:**
| Field | Type | Required |
|---|---|---|
| sales_order_detail_id | int | yes |
| line_item_id | UUID | no |

**Response `200`** — `StandardInventoryResponse[List[SalesOrderReleaseData]]`:
```json
[ { "release_id": 0, "sales_order_detail_id": 0, "ar_invt_id": 0, "seq": 0, "qty": 0.0,
    "original_qty": 0.0, "shipped_qty": 0.0, "left_to_ship": 0.0, "request_date": "datetime",
    "promise_date": "datetime", "must_ship_date": "datetime", "ship_date": "datetime",
    "forecast": "string", "date_type": "string", "acknowledged": false, "expedite": false } ]
```

**HTTP errors:** none

**exception_logs code:** none

**decision enum:** none

---

## `/quote-processing/record-exception`

**Request:**
| Field | Type | Required | Default |
|---|---|---|---|
| processing_id | UUID | yes | — |
| line_item_id | UUID | no | null |
| agent_name | string | yes | — |
| tool_name | string | yes | — |
| exception_code | string | yes | — |
| exception_message | string | yes | — |
| retry_attempt | int | no | 0 |
| max_retry_count | int | no | 0 |
| is_retryable | bool | no | false |
| resolved | bool | no | false |
| resolved_by | string | no | null |
| resolved_time | datetime | no | null |

**Response `201`** — `StandardResponse[ExceptionRecordData]`:
```json
{ "exception_id": "UUID", "processing_id": "UUID", "line_item_id": "UUID|null", "agent_name": "string",
  "tool_name": "string", "exception_code": "string", "exception_message": "string",
  "retry_attempt": 0, "max_retry_count": 0, "is_retryable": false, "resolved": false,
  "resolved_by": "string|null", "resolved_time": "datetime|null", "created_at": "datetime" }
```

**HTTP errors:** none

**exception_logs code:** caller-supplied (`exception_code` is free text, not an enum)

---

## `/quote-processing/move-quote-file`

**Request:**
| Field | Type | Required |
|---|---|---|
| filename | string | yes |
| from_folder | string | yes |
| to_folder | string | yes |
| outcome | enum: `SUCCESS` \| `FAILED` \| `PARTIAL` | yes |

**Response `200`** — `StandardResponse[MoveQuoteFileResponseData]`:
```json
{ "filename": "string", "outcome": "SUCCESS|FAILED|PARTIAL", "source_path": "string",
  "destination_path": "string", "moved": true }
```

**HTTP errors:** `404 QUOTE_FILE_NOT_FOUND`

**exception_logs code:** `QUOTE_FILE_MOVE_FAILED`

---

## `/notifications/send-email`

**Request:**
| Payload key | Type | Required |
|---|---|---|
| `from` | string | yes |
| `password` | string | yes |
| `to` | string[] (min 1) | yes |
| `subject` | string | yes |
| `body` | string | yes |
| `processing_id` | UUID | no |

**Response `200`** — `StandardResponse[SendNotificationResponseData]`:
```json
{ "subject": "string", "delivery_status": "SENT|FAILED|PARTIAL",
  "recipients": [ { "notification_id": "UUID", "recipient": "string",
    "delivery_status": "SENT|FAILED", "sent_time": "datetime|null", "error": "string|null" } ] }
```

**HTTP errors:** none

**exception_logs code:** none

**delivery_status enum (top-level):** `SENT` | `FAILED` | `PARTIAL`
**delivery_status enum (per-recipient):** `SENT` | `FAILED`

---

## `/reporting/quote-summary`

**Request:**
| Field | Type | Required |
|---|---|---|
| processing_id | UUID | yes |

**Response `200`** — `StandardResponse[QuoteSummaryData]`:
```json
{
  "processing_id": "UUID",
  "quote_number": "string|null",
  "status": "string|null",
  "effective_date": "string|null",
  "customer_name": "string|null",
  "ar_cust_id": "string|null",
  "report_id": "string|null",
  "total_lines": 0,
  "successful_lines": 0,
  "review_required_lines": 0,
  "failed_lines": 0,
  "exception_count": 0,
  "line_items": [
    {
      "line_item_id": "UUID",
      "evco_mfg_number": "string|null",
      "evco_part_id": "string|null",
      "customer_part_number": "string|null",
      "aka": { "decision": "string|null", "aka_id": "string|null", "before": "object|null", "after": "object|null" },
      "pricing": {
        "decision": "string|null", "before_price": "float|null", "after_price": "float|null",
        "tiers": [ { "quantity": 0, "price": 0.0, "is_active": true, "inactive_date": "string|null",
          "created_by_line_item_id": "UUID|null", "inactivated_by_line_item_id": "UUID|null" } ]
      },
      "sales_order": { "decision": "string|null",
        "orders": [ { "sales_order_id": "string|null", "decision": "string|null", "note": "string|null" } ] },
      "exceptions": ["string"],
      "line_status": "string|null"
    }
  ]
}
```

**HTTP errors:** `404 QUOTE_PROCESSING_NOT_FOUND`

**exception_logs code:** none

**Enums:**
| Field | Values |
|---|---|
| `aka.decision` | `NOT_FOUND` \| `CREATED` \| `NO_CHANGE` \| `UPDATED` |
| `pricing.decision` | `NOT_FOUND` \| `CREATED` \| `NO_CHANGE` \| `UPDATED` |
| `sales_order.decision` (per-order and overall) | `NOT_FOUND` \| `NO_CHANGE` \| `MISMATCH` |

---

## All exception_logs codes

| Code | Endpoint |
|---|---|
| `AKA_NOT_FOUND` | `/inventory/get-aka` |
| `AKA_UPDATE_TARGET_MISSING` | `/inventory/update-aka` |
| `PRICE_NOT_FOUND` | `/inventory/get-pricebreaks` |
| `PRICE_UPDATE_TARGET_MISSING` | `/inventory/update-pricebreak` |
| `QUOTE_FILE_MOVE_FAILED` | `/quote-processing/move-quote-file` |
| *(caller-defined)* | `/quote-processing/record-exception` |
