SELECT DISTINCT
    service.qualified_name AS service_name,
    impl.qualified_name AS implementation,
    aidl.qualified_name AS binder_interface,
    registration.source_path,
    registration.line_start
FROM edge registered
JOIN node impl
  ON impl.node_id = registered.from_node_id
JOIN node service
  ON service.node_id = registered.to_node_id
JOIN node registration
  ON registration.node_type = 'SERVICE_REGISTRATION'
JOIN edge registration_key
  ON registration_key.from_node_id =
     registration.node_id
 AND registration_key.to_node_id =
     service.node_id
 AND registration_key.edge_type =
     'REGISTERS_BINDER_NAME'
JOIN edge registration_instance
  ON registration_instance.from_node_id =
     registration.node_id
 AND registration_instance.to_node_id =
     impl.node_id
 AND registration_instance.edge_type =
     'REGISTERS_INSTANCE'
LEFT JOIN edge binder
  ON binder.from_node_id = impl.node_id
 AND binder.edge_type = 'IMPLEMENTS_BINDER'
LEFT JOIN node aidl
  ON aidl.node_id = binder.to_node_id
WHERE registered.edge_type = 'REGISTERED_AS'
  AND service.qualified_name = 'activity'
ORDER BY implementation;
