CREATE TABLE IF NOT EXISTS perfil (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  descricao TEXT NOT NULL DEFAULT '',
  num_comentarios INT,
  num_visualizacoes INT,
  num_reacoes INT,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reel (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  descricao TEXT NOT NULL DEFAULT '',
  num_comentarios INT,
  num_visualizacoes INT,
  num_reacoes INT,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS post (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  descricao TEXT NOT NULL DEFAULT '',
  num_comentarios INT,
  num_visualizacoes INT,
  num_reacoes INT,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS storie (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  descricao TEXT NOT NULL DEFAULT '',
  num_comentarios INT,
  num_visualizacoes INT,
  num_reacoes INT,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS foto (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  descricao TEXT NOT NULL DEFAULT '',
  num_comentarios INT,
  num_visualizacoes INT,
  num_reacoes INT,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS permalink (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte TEXT NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'finished',
  error TEXT NOT NULL DEFAULT '',
  data TIMESTAMP NULL,
  perfil TEXT NOT NULL DEFAULT '',
  verificado BOOLEAN,
  descricao TEXT NOT NULL DEFAULT '',
  num_comentarios INT,
  num_visualizacoes INT,
  num_reacoes INT,
  ultima_att TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_perfil_status ON perfil(status);
CREATE INDEX IF NOT EXISTS idx_video_status ON video(status);
CREATE INDEX IF NOT EXISTS idx_reel_status ON reel(status);
CREATE INDEX IF NOT EXISTS idx_post_status ON post(status);
CREATE INDEX IF NOT EXISTS idx_storie_status ON storie(status);
CREATE INDEX IF NOT EXISTS idx_foto_status ON foto(status);
CREATE INDEX IF NOT EXISTS idx_permalink_status ON permalink(status);
