SELECT
  json_extract(properties_json, '$.analysis_scope') AS analysis_scope,
  CAST(json_extract(properties_json, '$.full_aosp_coverage') AS INTEGER)
    AS full_aosp_coverage,
  CASE json_extract(properties_json, '$.analysis_scope')
    WHEN 'partial' THEN 'PARTIAL SOURCE GRAPH - NOT FULL AOSP'
    ELSE ''
  END AS warning,
  json_extract(properties_json, '$.source_scope_sha256')
    AS scope_report_sha256
FROM node
WHERE node_type = 'GRAPH_BUILD'
ORDER BY qualified_name DESC
LIMIT 1;
