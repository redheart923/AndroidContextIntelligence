SELECT json_extract(properties_json, '$.repository') AS repository,
       node_type, COUNT(*) AS count
FROM node
WHERE json_extract(properties_json, '$.repository') IS NOT NULL
GROUP BY repository, node_type
ORDER BY repository, count DESC;
