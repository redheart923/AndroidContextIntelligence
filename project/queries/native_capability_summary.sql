SELECT node_type AS fact_kind, COUNT(*) AS count
FROM effective_node
WHERE node_type LIKE 'C_%'
   OR node_type LIKE 'CPP_%'
   OR node_type LIKE 'RUST_%'
   OR node_type IN ('NATIVE_FUNCTION', 'SOONG_MODULE', 'BUILD_ACTION')
GROUP BY node_type
UNION ALL
SELECT edge_type, COUNT(*)
FROM effective_edge
WHERE edge_type IN ('INCLUDES', 'EXPORTS_C_ABI_SYMBOL', 'JNI_BINDS_TO')
GROUP BY edge_type
ORDER BY fact_kind;
