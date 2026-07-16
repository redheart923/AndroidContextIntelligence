PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS node (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  qualified_name TEXT,
  display_name TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_node_type ON node(node_type);
CREATE INDEX IF NOT EXISTS idx_node_name ON node(qualified_name);
CREATE INDEX IF NOT EXISTS idx_node_source ON node(source_path);

CREATE TABLE IF NOT EXISTS edge (
  edge_id TEXT PRIMARY KEY,
  edge_type TEXT NOT NULL,
  from_node_id TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL,
  FOREIGN KEY(from_node_id) REFERENCES node(node_id),
  FOREIGN KEY(to_node_id) REFERENCES node(node_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_from ON edge(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_to ON edge(to_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_type ON edge(edge_type);

