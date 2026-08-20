SELECT
  st.scenario_id,
  st.status,
  entry.qualified_name AS entry_method,
  sink.source_path,
  sink.line_start AS sink_line,
  st.guard_count,
  st.identity_transition_count,
  COUNT(step.ordinal) AS trace_step_count
FROM security_trace st
JOIN node entry ON entry.node_id = st.entry_method_id
JOIN call_site sink ON sink.call_site_id = st.sink_call_site_id
LEFT JOIN security_trace_step step ON step.trace_id = st.trace_id
GROUP BY st.trace_id
ORDER BY st.scenario_id, entry_method, sink.source_path, sink.line_start;
