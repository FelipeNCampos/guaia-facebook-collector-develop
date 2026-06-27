ALTER TABLE request_links
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_request_links_status_attempts
  ON request_links(status, attempt_count, created_at);
