SELECT
  display_name AS module_name,
  json_extract(properties_json, '$.module_kind') AS module_kind,
  source_path,
  line_start
FROM effective_node
WHERE node_type = 'SOONG_MODULE'
ORDER BY module_name, source_path;
