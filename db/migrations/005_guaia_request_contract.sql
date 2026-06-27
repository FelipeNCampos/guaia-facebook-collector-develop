CREATE SEQUENCE IF NOT EXISTS requests_id_query_seq AS BIGINT;

ALTER TABLE requests
  ADD COLUMN IF NOT EXISTS id_query BIGINT,
  ADD COLUMN IF NOT EXISTS subject TEXT,
  ADD COLUMN IF NOT EXISTS start_date TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS end_date TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS status_current SMALLINT,
  ADD COLUMN IF NOT EXISTS query_source SMALLINT,
  ADD COLUMN IF NOT EXISTS source_type SMALLINT;

UPDATE requests
SET
  subject = COALESCE(subject, termo),
  start_date = COALESCE(
    start_date,
    CASE
      WHEN filters ? 'after' AND NULLIF(filters->>'after', '') IS NOT NULL
        THEN (filters->>'after')::timestamptz
      ELSE NULL
    END
  ),
  end_date = COALESCE(
    end_date,
    CASE
      WHEN filters ? 'before' AND NULLIF(filters->>'before', '') IS NOT NULL
        THEN (filters->>'before')::timestamptz
      ELSE NULL
    END
  ),
  query_source = COALESCE(query_source, 5),
  source_type = COALESCE(source_type, 3),
  status_current = COALESCE(
    status_current,
    CASE status
      WHEN 'received' THEN 1
      WHEN 'queued' THEN 1
      WHEN 'processing' THEN 2
      WHEN 'finished' THEN 3
      WHEN 'failed' THEN 5
      WHEN 'failed_captcha' THEN 5
      ELSE 1
    END
  );

UPDATE requests
SET id_query = nextval('requests_id_query_seq')
WHERE id_query IS NULL;

SELECT setval(
  'requests_id_query_seq',
  GREATEST(COALESCE((SELECT MAX(id_query) FROM requests), 1), 1),
  true
);

ALTER TABLE requests
  ALTER COLUMN subject SET NOT NULL,
  ALTER COLUMN id_query SET NOT NULL,
  ALTER COLUMN status_current SET NOT NULL,
  ALTER COLUMN query_source SET NOT NULL,
  ALTER COLUMN source_type SET NOT NULL,
  ALTER COLUMN status_current SET DEFAULT 1,
  ALTER COLUMN query_source SET DEFAULT 5,
  ALTER COLUMN source_type SET DEFAULT 3;

CREATE UNIQUE INDEX IF NOT EXISTS idx_requests_id_query_unique ON requests (id_query);
