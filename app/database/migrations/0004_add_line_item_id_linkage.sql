-- Adds line_item_id linkage so an AKA/pricing/sales-order lookup made during
-- real quote processing can be traced back to the specific quote_line_items
-- row that triggered it. pricing_history already had a source_processing_id
-- column (added in migration 0001) that was simply never being populated by
-- the application code - that gap is fixed in app/modules/pricing/service.py
-- (not a duplicate column); line_item_id is added there too since no
-- line-item-level column existed yet, only the coarser processing-level one.
ALTER TABLE aka_records ADD COLUMN IF NOT EXISTS line_item_id UUID REFERENCES quote_line_items(line_item_id);
ALTER TABLE pricing_history ADD COLUMN IF NOT EXISTS line_item_id UUID REFERENCES quote_line_items(line_item_id);
ALTER TABLE sales_orders_synced ADD COLUMN IF NOT EXISTS line_item_id UUID REFERENCES quote_line_items(line_item_id);
ALTER TABLE sales_order_releases_synced ADD COLUMN IF NOT EXISTS line_item_id UUID REFERENCES quote_line_items(line_item_id);

CREATE INDEX IF NOT EXISTS ix_aka_records_line_item ON aka_records (line_item_id);
CREATE INDEX IF NOT EXISTS ix_pricing_history_line_item ON pricing_history (line_item_id);
CREATE INDEX IF NOT EXISTS ix_sales_orders_synced_line_item ON sales_orders_synced (line_item_id);
