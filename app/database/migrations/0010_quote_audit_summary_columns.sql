-- Quote audit summary columns: written by LineItemAuditRepository.finalize_quote_audit
-- (UPDATE quote_audit SET total_lines, ... status = 'COMPLETED') and incremented by
-- QuoteProcessingRepository (exception_count). Created by no earlier migration, so the
-- UPDATE failed with UndefinedColumn on "total_lines".

ALTER TABLE quote_audit
ADD COLUMN IF NOT EXISTS total_lines            INTEGER,
ADD COLUMN IF NOT EXISTS successful_lines       INTEGER,
ADD COLUMN IF NOT EXISTS review_required_lines  INTEGER,
ADD COLUMN IF NOT EXISTS failed_lines           INTEGER,
ADD COLUMN IF NOT EXISTS exception_count        INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS report_id              TEXT,
ADD COLUMN IF NOT EXISTS completed_time         TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS status                 TEXT;
