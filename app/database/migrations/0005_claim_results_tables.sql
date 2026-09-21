-- Claims 4 tables that were already live in evco_db with no migration file
-- backing them (found while mapping the schema). Column shapes below were
-- read directly from information_schema on the live DB — this is
-- CREATE TABLE IF NOT EXISTS, so it changes nothing where they already
-- exist; it only lets a fresh database be brought up to the same shape.
--
-- error_code_catalog (also found untracked) is intentionally NOT claimed
-- here - it's a plain reference/lookup table, out of scope for the Quote
-- Execution Summary report this migration supports.

CREATE TABLE IF NOT EXISTS aka_resolution_results (
    aka_result_id   UUID PRIMARY KEY,
    line_item_id    UUID REFERENCES quote_line_items(line_item_id),
    aka_id          VARCHAR,
    ar_invt_id      VARCHAR,
    ar_cust_id      VARCHAR,
    bom_id          VARCHAR,
    decision        VARCHAR,
    status          VARCHAR,
    before_value    JSONB,
    after_value     JSONB,
    created_at      TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pricing_results (
    pricing_result_id      UUID PRIMARY KEY,
    line_item_id           UUID REFERENCES quote_line_items(line_item_id),
    pricing_action         VARCHAR,
    pricing_before_value   NUMERIC,
    pricing_after_value    NUMERIC,
    inactivated_price_rows JSONB,
    effective_pricing      JSONB,
    decision               VARCHAR,
    status                 VARCHAR,
    created_at             TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sales_order_results (
    sales_order_result_id  UUID PRIMARY KEY,
    line_item_id           UUID REFERENCES quote_line_items(line_item_id),
    sales_order_hold       BOOLEAN,
    sales_order_id         VARCHAR,
    sales_order_note       TEXT,
    updates                JSONB,
    decision               VARCHAR,
    status                 VARCHAR,
    created_at             TIMESTAMP DEFAULT now(),
    notes                  VARCHAR
);

CREATE TABLE IF NOT EXISTS line_item_audit (
    line_item_audit_id     UUID PRIMARY KEY,
    line_item_id           UUID REFERENCES quote_line_items(line_item_id),
    quote_id                VARCHAR,
    quote_part_id            VARCHAR,
    part_number              VARCHAR,
    ar_invt_id               VARCHAR,
    aka_id                   VARCHAR,
    mold_check_result        VARCHAR,
    moq_check_result         VARCHAR,
    pricing_action           VARCHAR,
    pricing_before_value     NUMERIC,
    pricing_after_value      NUMERIC,
    inactivated_price_rows   JSONB,
    sales_order_hold         BOOLEAN,
    sales_order_id           VARCHAR,
    sales_order_note         TEXT,
    rule_codes_applied       JSONB,
    line_status              VARCHAR,
    completed_time           TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_aka_resolution_results_line_item ON aka_resolution_results (line_item_id);
CREATE INDEX IF NOT EXISTS ix_pricing_results_line_item ON pricing_results (line_item_id);
CREATE INDEX IF NOT EXISTS ix_sales_order_results_line_item ON sales_order_results (line_item_id);
CREATE INDEX IF NOT EXISTS ix_line_item_audit_line_item ON line_item_audit (line_item_id);
