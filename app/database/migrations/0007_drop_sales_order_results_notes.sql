-- sales_order_results.notes was never written by any code (sales_order_note is
-- the column actually populated) and no distinct meaning was ever found for it -
-- dropping it rather than carrying two copies of the same value forward.
ALTER TABLE sales_order_results DROP COLUMN IF EXISTS notes;
