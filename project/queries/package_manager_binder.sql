SELECT
    impl.qualified_name AS implementation,
    aidl.qualified_name AS binder_interface,
    impl.source_path
FROM edge e
JOIN node impl ON impl.node_id = e.from_node_id
JOIN node aidl ON aidl.node_id = e.to_node_id
WHERE e.edge_type = 'IMPLEMENTS_BINDER'
  AND aidl.qualified_name =
      'android.content.pm.IPackageManager'
ORDER BY impl.qualified_name;
