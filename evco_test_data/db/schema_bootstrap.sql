-- evco_test_data/db/schema_bootstrap.sql
--
-- No DDL/migration files exist anywhere in the real project today
-- (app/database/migrations/ is empty, app/database/base.py has no ORM model
-- layer) - the only place any table shape is defined is implicitly inside
-- the raw SQL strings in the 4 existing repository files:
--   app/database/quote_processing_repository.py
--   app/database/quote_scan_repository.py
--   app/database/notification_repository.py
--   app/database/api_audit_log_repository.py
--
-- This script formalizes those 9 already-implied tables (column names/order
-- read directly from those repositories' INSERT/SELECT statements) plus ONE
-- genuinely new table, pricing_history, so a scratch Postgres test database
-- can be created at all. It changes no existing repository code and adds no
-- columns beyond what those repositories already read/write.
--
-- Usage: psql "$DATABASE_URL" -f evco_test_data/db/schema_bootstrap.sql

CREATE TABLE IF NOT EXISTS quote_processing (
    processing_id           UUID PRIMARY KEY,
    scan_file_id            UUID,
    quote_id                UUID,
    filename                TEXT NOT NULL,
    received_time           TIMESTAMPTZ,
    processing_status       TEXT NOT NULL,
    current_agent           TEXT,
    retry_count             INTEGER NOT NULL DEFAULT 0,
    raw_extracted_json      JSONB,
    completed_at            TIMESTAMPTZ,
    processing_duration_ms  INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_audit (
    quote_audit_id   UUID PRIMARY KEY,
    processing_id    UUID NOT NULL REFERENCES quote_processing(processing_id),
    quote_id         UUID,
    filename         TEXT,
    received_time    TIMESTAMPTZ,
    customer_name    TEXT,
    customer_no      TEXT,
    effective_date   TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_line_items (
    line_item_id           UUID PRIMARY KEY,
    processing_id          UUID NOT NULL REFERENCES quote_processing(processing_id),
    quote_id               UUID,
    quote_part_id          TEXT,
    line_number            INTEGER,
    evco_part_id           TEXT,
    customer_part_number   TEXT,
    description            TEXT,
    bom_id                 TEXT,
    mold_number            TEXT,
    moq                    NUMERIC,
    price                  NUMERIC,
    parts                  JSONB,
    status                 TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exception_logs (
    exception_id      UUID PRIMARY KEY,
    line_item_id       UUID REFERENCES quote_line_items(line_item_id),
    processing_id      UUID REFERENCES quote_processing(processing_id),
    agent_name         TEXT,
    tool_name          TEXT,
    exception_code     TEXT NOT NULL,
    exception_message  TEXT,
    retry_attempt      INTEGER DEFAULT 0,
    max_retry_count    INTEGER,
    is_retryable       BOOLEAN DEFAULT false,
    resolved           BOOLEAN DEFAULT false,
    resolved_by        TEXT,
    resolved_time      TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_resolution_results (
    customer_resolution_id  UUID PRIMARY KEY,
    line_item_id            UUID NOT NULL REFERENCES quote_line_items(line_item_id),
    customer_name           TEXT,
    customer_no             TEXT,
    resolution_status       TEXT,
    matched_customer        TEXT,
    candidate_customers     JSONB,
    status                  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_scans (
    scan_id             UUID PRIMARY KEY,
    scan_status         TEXT NOT NULL,
    total_files_found   INTEGER,
    successful_files    INTEGER DEFAULT 0,
    failed_files        INTEGER DEFAULT 0,
    scan_started_at     TIMESTAMPTZ,
    scan_completed_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_scan_files (
    scan_file_id  UUID PRIMARY KEY,
    scan_id       UUID NOT NULL REFERENCES quote_scans(scan_id),
    filename      TEXT NOT NULL,
    file_status   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sent_notifications (
    notification_id     UUID PRIMARY KEY,
    processing_id        UUID REFERENCES quote_processing(processing_id),
    recipient             TEXT NOT NULL,
    notification_type     TEXT,
    subject                TEXT,
    message                TEXT,
    delivery_status        TEXT,
    sent_time              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS api_audit_logs (
    api_log_id         UUID PRIMARY KEY,
    processing_id      UUID REFERENCES quote_processing(processing_id),
    line_item_id       UUID REFERENCES quote_line_items(line_item_id),
    agent_name         TEXT,
    tool_name          TEXT,
    endpoint           TEXT NOT NULL,
    http_method        TEXT NOT NULL,
    request_payload    JSONB,
    response_payload   JSONB,
    http_status        INTEGER,
    retry_attempt      INTEGER DEFAULT 0,
    duration_ms        INTEGER,
    status             TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- Genuinely new table -----------------------------------------------
-- No table anywhere (mock or real) currently persists pricing or its
-- history - PriceBreakService (app/modules/pricing/service.py) is a pure
-- in-memory mock, lost on restart. This table exists purely to satisfy the
-- "preserve previous pricing, insert new version, mark old inactive"
-- requirement (BR-015/BR-016), which cannot be represented in any existing
-- table. It is written to ADDITIVELY, after the existing PriceBreakService
-- methods already return their result (see mock_api/pricing_history_hook.py)
-- - it does not alter that service's own decision logic.
CREATE TABLE IF NOT EXISTS pricing_history (
    pricing_history_id       UUID PRIMARY KEY,
    evco_part_number         TEXT NOT NULL,
    customer_number          TEXT NOT NULL,
    manufacturing_bom_number TEXT,
    quantity                 INTEGER NOT NULL,
    price                    NUMERIC NOT NULL,
    currency                 TEXT NOT NULL DEFAULT 'USD',
    effective_date           TIMESTAMPTZ NOT NULL,
    inactive_date            TIMESTAMPTZ,
    is_active                BOOLEAN NOT NULL DEFAULT true,
    source_quote_number      TEXT,
    source_processing_id     UUID REFERENCES quote_processing(processing_id),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_pricing_history_lookup
    ON pricing_history (customer_number, evco_part_number, manufacturing_bom_number, is_active);

-- --- Genuinely new tables (inventory/AKA persistence) --------------------
-- InventoryMockStore (app/modules/inventory/store.py) is a pure in-memory
-- dict, lost on restart, with no DB table backing it at all. These tables
-- give the existing search_part/get_bom_candidates/get_aka/create_aka/
-- update_aka results somewhere durable to land - written to ADDITIVELY by
-- evco_test_data/mock_api/persistent_services.py, which calls the existing,
-- unmodified InventoryMockStore methods first and only then persists their
-- result. No inventory decision logic is duplicated here.
CREATE TABLE IF NOT EXISTS inventory_items (
    inventory_item_id  UUID PRIMARY KEY,
    evco_part_number   TEXT NOT NULL,
    item_number        TEXT NOT NULL,
    description        TEXT,
    inventory_class     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (evco_part_number)
);

CREATE TABLE IF NOT EXISTS bom_candidates (
    bom_candidate_id          UUID PRIMARY KEY,
    item_number               TEXT NOT NULL,
    manufacturing_bom_number  TEXT NOT NULL,
    bom_description           TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (item_number, manufacturing_bom_number)
);

CREATE TABLE IF NOT EXISTS aka_records (
    aka_record_id           UUID PRIMARY KEY,
    customer_number         TEXT NOT NULL,
    customer_part_number    TEXT NOT NULL,
    item_number             TEXT NOT NULL,
    aka_description         TEXT,
    item_description        TEXT,
    uom                     TEXT,
    currency                TEXT,
    manufacturing_bom_number TEXT,
    moq                     INTEGER,
    selling_multiples_of    INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_number, customer_part_number, item_number)
);

-- --- Genuinely new tables (sales order persistence) -----------------------
-- SalesOrderService (app/modules/sales_order/service.py) is a pure live
-- IQMS pass-through with zero local persistence. These tables let
-- evco_test_data/mock_api/persistent_services.py record what the (dummy or
-- real) Sales Order GET APIs returned, without changing SalesOrderService's
-- own request/response contract or decision logic at all.
CREATE TABLE IF NOT EXISTS sales_orders_synced (
    sales_order_id     BIGINT PRIMARY KEY,
    sales_order_detail_id BIGINT,
    ar_invt_id          BIGINT,
    order_number        TEXT,
    po_number           TEXT,
    customer_number     TEXT,
    company             TEXT,
    item_number         TEXT,
    description         TEXT,
    customer_item_number TEXT,
    status              TEXT,
    total_qty_ordered   NUMERIC,
    unit_price          NUMERIC,
    date_taken          TIMESTAMPTZ,
    delivery_date       TIMESTAMPTZ,
    source              TEXT NOT NULL DEFAULT 'dummy',
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sales_order_releases_synced (
    release_id              BIGINT PRIMARY KEY,
    sales_order_detail_id   BIGINT NOT NULL,
    qty                     NUMERIC,
    must_ship_date          TIMESTAMPTZ,
    ship_date               TIMESTAMPTZ,
    synced_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
