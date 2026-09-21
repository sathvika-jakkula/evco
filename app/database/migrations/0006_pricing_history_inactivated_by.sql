-- pricing_history.line_item_id (added in migration 0004) only ever records
-- which line item CREATED a row - nothing tracks which line item's update
-- caused an older row to be closed out (is_active=false). The reporting API
-- needs both to show tier provenance, so this adds the missing half rather
-- than overloading the existing column with two meanings.
ALTER TABLE pricing_history ADD COLUMN IF NOT EXISTS inactivated_by_line_item_id UUID REFERENCES quote_line_items(line_item_id);

CREATE INDEX IF NOT EXISTS ix_pricing_history_inactivated_by ON pricing_history (inactivated_by_line_item_id);
