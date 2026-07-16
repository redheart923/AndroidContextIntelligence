SELECT node_type, COUNT(*) AS count
FROM node
GROUP BY node_type
ORDER BY count DESC;

SELECT edge_type, COUNT(*) AS count
FROM edge
GROUP BY edge_type
ORDER BY count DESC;
