ALTER TABLE correction_application RENAME TO correction_application_v3;
ALTER TABLE fact_correction RENAME TO fact_correction_v3;

CREATE TABLE fact_correction (
  correction_id TEXT PRIMARY KEY,
  action TEXT NOT NULL CHECK(action IN (
    'suppress', 'replace', 'annotate', 'add',
    'promote_candidate', 'suppress_candidate', 'replace_binding'
  )),
  target_fact_uri TEXT NOT NULL,
  expected_content_hash TEXT NOT NULL,
  applicable_source_revision TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  author TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  approval_ref TEXT NOT NULL,
  replacement_json TEXT,
  lifecycle_status TEXT NOT NULL CHECK(
    lifecycle_status IN ('draft', 'validated', 'active', 'stale')
  ),
  source_path TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

INSERT INTO fact_correction SELECT * FROM fact_correction_v3;

CREATE TABLE correction_application (
  correction_id TEXT NOT NULL REFERENCES fact_correction(correction_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  application_status TEXT NOT NULL CHECK(
    application_status IN ('applied', 'stale', 'conflict', 'rejected')
  ),
  target_content_hash TEXT,
  effective_fact_uri TEXT,
  message TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  PRIMARY KEY(correction_id, run_id)
);

INSERT INTO correction_application SELECT * FROM correction_application_v3;
DROP TABLE correction_application_v3;
DROP TABLE fact_correction_v3;

DROP VIEW effective_edge;
DROP VIEW effective_node;

CREATE VIEW effective_edge AS
SELECT
  e.*,
  (
    SELECT fc.replacement_json
    FROM fact_correction fc
    JOIN correction_application ca ON ca.correction_id = fc.correction_id
    WHERE fc.action = 'annotate'
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'edge:' || e.edge_id
    ORDER BY fc.correction_id LIMIT 1
  ) AS correction_annotation_json
FROM edge e
WHERE e.status = 'active'
  AND NOT EXISTS (
    SELECT 1 FROM node candidate
    WHERE candidate.node_type = 'EXTRACTION_CANDIDATE'
      AND candidate.node_id IN (e.from_node_id, e.to_node_id)
  )
  AND NOT EXISTS (
    SELECT 1
    FROM fact_correction fc
    JOIN correction_application ca ON ca.correction_id = fc.correction_id
    WHERE fc.action IN ('suppress', 'replace', 'replace_binding')
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'edge:' || e.edge_id
  );

CREATE VIEW effective_node AS
SELECT
  n.*,
  (
    SELECT fc.replacement_json
    FROM fact_correction fc
    JOIN correction_application ca ON ca.correction_id = fc.correction_id
    WHERE fc.action = 'annotate'
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'node:' || n.node_id
    ORDER BY fc.correction_id LIMIT 1
  ) AS correction_annotation_json
FROM node n
WHERE n.status = 'active'
  AND n.node_type <> 'EXTRACTION_CANDIDATE'
  AND NOT EXISTS (
    SELECT 1
    FROM fact_correction fc
    JOIN correction_application ca ON ca.correction_id = fc.correction_id
    WHERE fc.action IN ('suppress', 'replace', 'suppress_candidate')
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'node:' || n.node_id
  );
