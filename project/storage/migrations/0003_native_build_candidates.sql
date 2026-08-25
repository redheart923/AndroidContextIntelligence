DROP VIEW effective_edge;
DROP VIEW effective_node;

CREATE INDEX IF NOT EXISTS idx_node_candidate_status
  ON node(node_type, status)
  WHERE node_type = 'EXTRACTION_CANDIDATE';

CREATE VIEW effective_edge AS
SELECT
  e.*,
  (
    SELECT fc.replacement_json
    FROM fact_correction fc
    JOIN correction_application ca
      ON ca.correction_id = fc.correction_id
    WHERE fc.action = 'annotate'
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'edge:' || e.edge_id
    ORDER BY fc.correction_id
    LIMIT 1
  ) AS correction_annotation_json
FROM edge e
WHERE e.status = 'active'
  AND NOT EXISTS (
    SELECT 1
    FROM node candidate
    WHERE candidate.node_type = 'EXTRACTION_CANDIDATE'
      AND candidate.node_id IN (e.from_node_id, e.to_node_id)
  )
  AND NOT EXISTS (
    SELECT 1
    FROM fact_correction fc
    JOIN correction_application ca
      ON ca.correction_id = fc.correction_id
    WHERE fc.action IN ('suppress', 'replace')
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'edge:' || e.edge_id
  );

CREATE VIEW effective_node AS
SELECT
  n.*,
  (
    SELECT fc.replacement_json
    FROM fact_correction fc
    JOIN correction_application ca
      ON ca.correction_id = fc.correction_id
    WHERE fc.action = 'annotate'
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'node:' || n.node_id
    ORDER BY fc.correction_id
    LIMIT 1
  ) AS correction_annotation_json
FROM node n
WHERE n.status = 'active'
  AND n.node_type <> 'EXTRACTION_CANDIDATE'
  AND NOT EXISTS (
    SELECT 1
    FROM fact_correction fc
    JOIN correction_application ca
      ON ca.correction_id = fc.correction_id
    WHERE fc.action IN ('suppress', 'replace')
      AND ca.application_status = 'applied'
      AND fc.target_fact_uri = 'node:' || n.node_id
  );
