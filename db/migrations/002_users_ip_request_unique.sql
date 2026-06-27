CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_ip_request_unique
  ON usuarios (ip_request);

