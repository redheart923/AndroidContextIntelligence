SELECT
    key.qualified_name AS local_service_key,
    impl.qualified_name AS implementation,
    registration.source_path,
    registration.line_start
FROM edge exposed
JOIN node impl
  ON impl.node_id = exposed.from_node_id
JOIN node key
  ON key.node_id = exposed.to_node_id
LEFT JOIN node registration
  ON registration.node_type =
     'SERVICE_REGISTRATION'
LEFT JOIN edge registration_key
  ON registration_key.from_node_id =
     registration.node_id
 AND registration_key.to_node_id = key.node_id
 AND registration_key.edge_type =
     'REGISTERS_LOCAL_KEY'
LEFT JOIN edge registration_instance
  ON registration_instance.from_node_id =
     registration.node_id
 AND registration_instance.to_node_id =
     impl.node_id
 AND registration_instance.edge_type =
     'REGISTERS_INSTANCE'
WHERE exposed.edge_type =
      'EXPOSED_AS_LOCAL_SERVICE'
ORDER BY key.qualified_name, impl.qualified_name
LIMIT 100;
