CREATE TABLE extraction_run (
  run_id TEXT PRIMARY KEY,
  capability TEXT NOT NULL,
  database_fingerprint TEXT NOT NULL,
  source_fingerprint TEXT NOT NULL,
  product TEXT NOT NULL,
  variant TEXT NOT NULL,
  build_targets_json TEXT NOT NULL,
  codeql_version TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  query_pack_lock_hash TEXT NOT NULL,
  observed_file_count INTEGER NOT NULL CHECK(observed_file_count >= 0),
  observed_method_count INTEGER NOT NULL CHECK(observed_method_count >= 0),
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE extraction_evidence (
  evidence_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  query_pack TEXT NOT NULL,
  query_id TEXT NOT NULL,
  query_version TEXT NOT NULL,
  raw_result_hash TEXT NOT NULL,
  raw_result_path TEXT NOT NULL,
  database_fingerprint TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE semantic_definition (
  definition_id TEXT PRIMARY KEY REFERENCES node(node_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  logical_method_id TEXT REFERENCES node(node_id),
  semantic_symbol_key TEXT NOT NULL,
  language TEXT NOT NULL CHECK(language IN ('java', 'kotlin')),
  callable_kind TEXT NOT NULL,
  repository TEXT NOT NULL,
  source_path TEXT NOT NULL,
  line_start INTEGER NOT NULL CHECK(line_start >= 1),
  column_start INTEGER NOT NULL CHECK(column_start >= 1),
  line_end INTEGER NOT NULL CHECK(line_end >= line_start),
  column_end INTEGER NOT NULL CHECK(column_end >= 1),
  resolution_status TEXT NOT NULL CHECK(
    resolution_status IN ('unique', 'ambiguous', 'unmatched', 'synthetic')
  ),
  content_hash TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_semantic_definition_logical
  ON semantic_definition(logical_method_id);
CREATE INDEX idx_semantic_definition_symbol
  ON semantic_definition(semantic_symbol_key);

CREATE TABLE call_site (
  call_site_id TEXT PRIMARY KEY REFERENCES node(node_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  caller_method_id TEXT NOT NULL REFERENCES node(node_id),
  repository TEXT NOT NULL,
  source_path TEXT NOT NULL,
  line_start INTEGER NOT NULL CHECK(line_start >= 1),
  column_start INTEGER NOT NULL CHECK(column_start >= 1),
  line_end INTEGER NOT NULL CHECK(line_end >= line_start),
  column_end INTEGER NOT NULL CHECK(column_end >= 1),
  expression_hash TEXT NOT NULL,
  dispatch_kind TEXT NOT NULL,
  resolution_status TEXT NOT NULL CHECK(
    resolution_status IN ('resolved', 'ambiguous', 'unresolved', 'unsupported')
  ),
  candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
  content_hash TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_call_site_caller ON call_site(caller_method_id);
CREATE INDEX idx_call_site_source ON call_site(repository, source_path);

CREATE TABLE call_target (
  call_site_id TEXT NOT NULL REFERENCES call_site(call_site_id),
  callee_method_id TEXT NOT NULL REFERENCES node(node_id),
  relation_kind TEXT NOT NULL CHECK(relation_kind IN ('must', 'may')),
  evidence_id TEXT NOT NULL REFERENCES extraction_evidence(evidence_id),
  content_hash TEXT NOT NULL,
  PRIMARY KEY(call_site_id, callee_method_id, relation_kind)
);

CREATE INDEX idx_call_target_callee ON call_target(callee_method_id);

CREATE TABLE program_value (
  value_id TEXT PRIMARY KEY REFERENCES node(node_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  owner_method_id TEXT NOT NULL REFERENCES node(node_id),
  value_kind TEXT NOT NULL CHECK(
    value_kind IN ('parameter', 'return', 'field_read', 'field_write', 'expression')
  ),
  parameter_index INTEGER CHECK(parameter_index >= 0),
  declared_type TEXT,
  repository TEXT NOT NULL,
  source_path TEXT NOT NULL,
  line_start INTEGER NOT NULL CHECK(line_start >= 1),
  column_start INTEGER NOT NULL CHECK(column_start >= 1),
  line_end INTEGER NOT NULL CHECK(line_end >= line_start),
  column_end INTEGER NOT NULL CHECK(column_end >= 1),
  expression_hash TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  CHECK(
    (value_kind = 'parameter' AND parameter_index IS NOT NULL)
    OR (value_kind != 'parameter' AND parameter_index IS NULL)
  )
);

CREATE INDEX idx_program_value_owner ON program_value(owner_method_id);

CREATE TABLE dataflow_path (
  path_id TEXT PRIMARY KEY REFERENCES node(node_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  scenario_id TEXT NOT NULL,
  source_value_id TEXT NOT NULL REFERENCES program_value(value_id),
  sink_value_id TEXT NOT NULL REFERENCES program_value(value_id),
  path_kind TEXT NOT NULL,
  confidence_class TEXT NOT NULL,
  step_count INTEGER NOT NULL CHECK(step_count >= 2),
  path_fingerprint TEXT NOT NULL,
  evidence_id TEXT NOT NULL REFERENCES extraction_evidence(evidence_id),
  status TEXT NOT NULL,
  repository TEXT NOT NULL,
  source_path TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_dataflow_path_scenario ON dataflow_path(scenario_id);

CREATE TABLE dataflow_step (
  path_id TEXT NOT NULL REFERENCES dataflow_path(path_id),
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  value_id TEXT NOT NULL REFERENCES program_value(value_id),
  step_kind TEXT NOT NULL,
  repository TEXT NOT NULL,
  source_path TEXT NOT NULL,
  line_start INTEGER NOT NULL CHECK(line_start >= 1),
  column_start INTEGER NOT NULL CHECK(column_start >= 1),
  line_end INTEGER NOT NULL CHECK(line_end >= line_start),
  column_end INTEGER NOT NULL CHECK(column_end >= 1),
  message TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  PRIMARY KEY(path_id, ordinal)
);

CREATE TABLE security_trace (
  trace_id TEXT PRIMARY KEY REFERENCES node(node_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  scenario_id TEXT NOT NULL,
  entry_method_id TEXT NOT NULL REFERENCES node(node_id),
  sink_call_site_id TEXT NOT NULL REFERENCES call_site(call_site_id),
  guard_count INTEGER NOT NULL CHECK(guard_count >= 0),
  identity_transition_count INTEGER NOT NULL CHECK(identity_transition_count >= 0),
  trace_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,
  repository TEXT NOT NULL,
  source_path TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_security_trace_scenario ON security_trace(scenario_id);

CREATE TABLE security_trace_step (
  trace_id TEXT NOT NULL REFERENCES security_trace(trace_id),
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  step_kind TEXT NOT NULL CHECK(
    step_kind IN ('call_site', 'dataflow_path', 'guard', 'identity_clear', 'identity_restore')
  ),
  call_site_id TEXT REFERENCES call_site(call_site_id),
  dataflow_path_id TEXT REFERENCES dataflow_path(path_id),
  guard_call_site_id TEXT REFERENCES call_site(call_site_id),
  identity_call_site_id TEXT REFERENCES call_site(call_site_id),
  content_hash TEXT NOT NULL,
  PRIMARY KEY(trace_id, ordinal),
  CHECK(
    (call_site_id IS NOT NULL)
    + (dataflow_path_id IS NOT NULL)
    + (guard_call_site_id IS NOT NULL)
    + (identity_call_site_id IS NOT NULL) = 1
  )
);

CREATE TABLE fact_correction (
  correction_id TEXT PRIMARY KEY,
  action TEXT NOT NULL CHECK(action IN ('suppress', 'replace', 'annotate', 'add')),
  target_fact_uri TEXT NOT NULL,
  expected_content_hash TEXT NOT NULL,
  applicable_source_revision TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  author TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  approval_ref TEXT NOT NULL,
  replacement_json TEXT,
  lifecycle_status TEXT NOT NULL CHECK(
    lifecycle_status IN ('draft', 'validated', 'active', 'stale')
  ),
  source_path TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE correction_application (
  correction_id TEXT NOT NULL REFERENCES fact_correction(correction_id),
  run_id TEXT NOT NULL REFERENCES extraction_run(run_id),
  application_status TEXT NOT NULL CHECK(
    application_status IN ('applied', 'stale', 'conflict', 'rejected')
  ),
  target_content_hash TEXT,
  effective_fact_uri TEXT,
  message TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  PRIMARY KEY(correction_id, run_id)
);
