SELECT
  managed.qualified_name AS managed_symbol,
  native.qualified_name AS native_symbol,
  binding.source_path,
  binding.line_start,
  binding.properties_json
FROM effective_edge binding
JOIN effective_node managed ON managed.node_id = binding.from_node_id
JOIN effective_node native ON native.node_id = binding.to_node_id
WHERE binding.edge_type = 'JNI_BINDS_TO'
ORDER BY managed.qualified_name, native.qualified_name;
