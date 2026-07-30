SELECT
    logical.node_type AS logical_type,
    logical.qualified_name AS logical_symbol,
    json_extract(
        logical.properties_json,
        '$.definition_resolution'
    ) AS resolution,
    json_extract(
        definition.properties_json,
        '$.repository'
    ) AS repository,
    definition.source_path,
    definition.line_start
FROM edge link
JOIN node definition
  ON definition.node_id = link.from_node_id
JOIN node logical
  ON logical.node_id = link.to_node_id
WHERE link.edge_type = 'DEFINES_SYMBOL'
ORDER BY
    logical.node_type,
    logical.qualified_name,
    repository,
    definition.source_path,
    definition.line_start;
