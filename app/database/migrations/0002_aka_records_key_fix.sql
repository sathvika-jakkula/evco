-- Fixes aka_records' identity key to match the redesigned AKA API contract:
-- an AKA mapping is now identified by (customer_number, item_number,
-- manufacturing_bom_number) - akaItem# (customer_part_number) became a
-- mutable, updatable attribute (see UpdateAkaRequest in
-- app/modules/inventory/schemas.py), so it can no longer be part of the
-- uniqueness key.
ALTER TABLE aka_records
    DROP CONSTRAINT IF EXISTS aka_records_customer_number_customer_part_number_item_numbe_key;

ALTER TABLE aka_records
    ADD CONSTRAINT aka_records_customer_item_mfg_key UNIQUE (customer_number, item_number, manufacturing_bom_number);
