-- aka_records was missing 3 fields the AKA API contract actually accepts
-- (rev, customername, shipToAttn - see AkaDetail in
-- app/modules/inventory/schemas.py) and had no way to tell whether a row's
-- last operation was a create, an update, or just a read.
ALTER TABLE aka_records ADD COLUMN IF NOT EXISTS rev TEXT NOT NULL DEFAULT '';
ALTER TABLE aka_records ADD COLUMN IF NOT EXISTS customer_name TEXT NOT NULL DEFAULT '';
ALTER TABLE aka_records ADD COLUMN IF NOT EXISTS ship_to_attn TEXT NOT NULL DEFAULT '';
ALTER TABLE aka_records ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'CREATED';
