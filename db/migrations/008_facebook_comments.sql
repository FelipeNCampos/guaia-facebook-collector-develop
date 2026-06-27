CREATE TABLE IF NOT EXISTS facebook_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url TEXT NOT NULL,
  runner_type VARCHAR(32) NOT NULL,
  comment_id TEXT NOT NULL,
  author_name TEXT NOT NULL DEFAULT '',
  author_id TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  likes INT,
  created_at TIMESTAMP NULL,
  collected_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE (source_url, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_facebook_comments_source_url
  ON facebook_comments(source_url);
