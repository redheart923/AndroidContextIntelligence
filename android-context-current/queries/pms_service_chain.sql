WITH RECURSIVE inheritance(
    class_id,
    ancestor_id,
    depth,
    visited
) AS (
    SELECT
        e.from_node_id,
        e.to_node_id,
        1,
        e.from_node_id || '|' || e.to_node_id
    FROM edge e
    WHERE e.edge_type = 'EXTENDS'

    UNION ALL

    SELECT
        inheritance.class_id,
        e.to_node_id,
        inheritance.depth + 1,
        inheritance.visited || '|' || e.to_node_id
    FROM inheritance
    JOIN edge e
      ON e.from_node_id = inheritance.ancestor_id
    WHERE e.edge_type = 'EXTENDS'
      AND inheritance.depth < 20
      AND instr(
          inheritance.visited,
          e.to_node_id
      ) = 0
)
SELECT DISTINCT
    service.qualified_name AS service_name,
    impl.qualified_name AS implementation,
    base.qualified_name AS binder_base,
    aidl.qualified_name AS binder_interface,
    inheritance.depth,
    registration.source_path,
    registration.line_start
FROM edge registered
JOIN node impl
  ON impl.node_id = registered.from_node_id
JOIN node service
  ON service.node_id = registered.to_node_id
JOIN node registration
  ON registration.node_type =
     'SERVICE_REGISTRATION'
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
JOIN inheritance
  ON inheritance.class_id = impl.node_id
JOIN edge binder
  ON binder.from_node_id =
     inheritance.ancestor_id
 AND binder.edge_type = 'IMPLEMENTS_BINDER'
JOIN node base
  ON base.node_id = inheritance.ancestor_id
JOIN node aidl
  ON aidl.node_id = binder.to_node_id
WHERE registered.edge_type = 'REGISTERED_AS'
  AND service.qualified_name = 'package'
ORDER BY inheritance.depth;
