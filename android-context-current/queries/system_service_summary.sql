SELECT node_type, COUNT(*) AS count
FROM node
WHERE node_type IN (
    'SERVICE_REGISTRATION',
    'BINDER_SERVICE_NAME',
    'LOCAL_SERVICE_KEY'
)
GROUP BY node_type
ORDER BY node_type;

SELECT edge_type, COUNT(*) AS count
FROM edge
WHERE edge_type IN (
    'REGISTERS_BINDER_NAME',
    'REGISTERS_LOCAL_KEY',
    'REGISTERS_INSTANCE',
    'REGISTERED_AS',
    'EXPOSED_AS_LOCAL_SERVICE'
)
GROUP BY edge_type
ORDER BY edge_type;

SELECT
    json_extract(
        properties_json,
        '$.resolution_status'
    ) AS resolution_status,
    COUNT(*) AS count
FROM node
WHERE node_type = 'SERVICE_REGISTRATION'
GROUP BY resolution_status
ORDER BY count DESC;
