-- quote_line_items.parts holds the part's box quantity (written from part.box_quantity by
-- QuoteProcessingRepository). Migration 0001 created it as JSONB, which rejects the integer
-- parameter psycopg sends ("column parts is of type jsonb but expression is of type smallint").
-- Production already has INTEGER; this brings databases still on JSONB in line with it.
-- Only converts when the column is currently jsonb, so it is a no-op where it is already integer.
-- Fails loudly if any existing jsonb value is not a plain integer (e.g. an object or array).

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'quote_line_items' AND column_name = 'parts' AND data_type = 'jsonb'
    ) THEN
        ALTER TABLE quote_line_items
            ALTER COLUMN parts TYPE INTEGER
            USING NULLIF(parts #>> '{}', '')::INTEGER;
    END IF;
END $$;
