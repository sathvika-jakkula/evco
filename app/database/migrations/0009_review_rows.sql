-- Review rows: the rows each review agent (AKA, PRICING, SALES_ORDER) returns for one
-- quote line. One call per line and area replaces that area's rows for the line, so a
-- retried call never duplicates rows. Rows are read back by getQuoteSummary.

CREATE TABLE IF NOT EXISTS review_rows (
    review_row_id            UUID PRIMARY KEY,
    processing_id            UUID NOT NULL REFERENCES quote_processing(processing_id),
    line_item_id             UUID NOT NULL REFERENCES quote_line_items(line_item_id),
    review_area              TEXT NOT NULL CHECK (review_area IN ('AKA', 'PRICING', 'SALES_ORDER')),
    quote_number             TEXT NOT NULL,
    line_number              INTEGER,
    evco_part_number         TEXT,
    mold_number              TEXT,
    manufacturing_bom_number TEXT,
    customer_number          TEXT,
    sort_order               INTEGER NOT NULL,
    rule_code                TEXT NOT NULL,
    check_name               TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN ('UPDATE_REQUIRED', 'NO_ACTION', 'NOT_VERIFIABLE')),
    message                  TEXT NOT NULL,
    field                    TEXT,
    record_ref               TEXT,
    sales_order_id           TEXT,
    sales_order_detail_id    TEXT,
    release_id               TEXT,
    quote_value              TEXT,
    system_value             TEXT,
    proposed_value           TEXT,
    exception_id            UUID,
    created_at               TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_review_rows_processing ON review_rows (processing_id);
CREATE INDEX IF NOT EXISTS ix_review_rows_line_area ON review_rows (line_item_id, review_area);
