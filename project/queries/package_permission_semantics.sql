-- Usage:
--   sqlite3 -header -column data/android_context.db
--   .parameter init
--   .parameter set :package_name com.android.example
--   .read queries/package_permission_semantics.sql
SELECT
  package.qualified_name AS package_name,
  edge.edge_type,
  permission.qualified_name AS permission_name,
  edge.source_path,
  edge.line_start,
  edge.properties_json
FROM edge
JOIN node package ON package.node_id = edge.from_node_id
JOIN node permission ON permission.node_id = edge.to_node_id
WHERE edge.status = 'active'
  AND package.node_type = 'ANDROID_PACKAGE'
  AND package.qualified_name = :package_name
  AND edge.edge_type IN (
    'REQUESTS_PERMISSION',
    'ALLOWLISTS_PRIVILEGED_PERMISSION',
    'DENIES_PRIVILEGED_PERMISSION',
    'DEFAULT_GRANTS_PERMISSION'
  )
ORDER BY edge.edge_type, permission.qualified_name, edge.source_path, edge.line_start;
