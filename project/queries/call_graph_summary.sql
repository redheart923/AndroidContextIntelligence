SELECT
  COALESCE(ct.relation_kind, 'none') AS relation_kind,
  json_extract(cs.properties_json, '$.language') AS language,
  cs.repository,
  cs.resolution_status,
  COUNT(DISTINCT cs.call_site_id) AS call_site_count,
  COUNT(ct.callee_method_id) AS target_count
FROM call_site cs
LEFT JOIN call_target ct ON ct.call_site_id = cs.call_site_id
GROUP BY
  COALESCE(ct.relation_kind, 'none'),
  json_extract(cs.properties_json, '$.language'),
  cs.repository,
  cs.resolution_status
ORDER BY relation_kind, language, cs.repository, cs.resolution_status;
