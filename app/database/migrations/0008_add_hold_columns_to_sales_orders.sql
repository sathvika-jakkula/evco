-- Add on_hold and ship_hold columns to sales_orders_synced table
ALTER TABLE sales_orders_synced
ADD COLUMN IF NOT EXISTS on_hold BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS ship_hold BOOLEAN DEFAULT FALSE;
