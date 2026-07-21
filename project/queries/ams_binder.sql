SELECT
    impl.qualified_name AS implementation,
    aidl.qualified_name AS binder_interface,
    aidl.source_path AS aidl_file
FROM edge e
JOIN node impl ON impl.node_id = e.from_node_id
JOIN node aidl ON aidl.node_id = e.to_node_id
WHERE e.edge_type = 'IMPLEMENTS_BINDER'
  AND impl.qualified_name =
      'com.android.server.am.ActivityManagerService';
