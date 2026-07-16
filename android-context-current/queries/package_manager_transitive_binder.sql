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
    impl.qualified_name AS implementation,
    base.qualified_name AS binder_base,
    aidl.qualified_name AS binder_interface,
    inheritance.depth
FROM inheritance
JOIN edge binder_edge
  ON binder_edge.from_node_id =
     inheritance.ancestor_id
 AND binder_edge.edge_type =
     'IMPLEMENTS_BINDER'
JOIN node impl
  ON impl.node_id = inheritance.class_id
JOIN node base
  ON base.node_id = inheritance.ancestor_id
JOIN node aidl
  ON aidl.node_id = binder_edge.to_node_id
WHERE impl.qualified_name =
      'com.android.server.pm.PackageManagerService.IPackageManagerImpl'
ORDER BY inheritance.depth;
