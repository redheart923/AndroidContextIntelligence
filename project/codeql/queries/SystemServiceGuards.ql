/**
 * @name Android system-service dominating guards
 * @description Exports configured fail-closed permission enforcement calls that dominate a sensitive sink.
 * @kind table
 * @id android-context/system-service-guards
 */

import java
import semmle.code.java.controlflow.Dominance
import lib.ExportHelpers
import lib.SystemServiceModels

from Call guard, Call sink
where
  isConfiguredGuard(guard) and isConfiguredSink(sink) and
  guard.getCaller() = sink.getCaller() and
  dominates(guard.getControlFlowNode(), sink.getControlFlowNode())
select
  schemaVersion() as schema_version,
  symbolKey(sink.getCaller()) as owner_symbol_key,
  guard.getCallee().getQualifiedName() as guard_callable,
  guard.getLocation().getStartLine() as guard_line,
  sink.getCallee().getQualifiedName() as sink_callable,
  sink.getLocation().getStartLine() as sink_line,
  "dominates" as relation_kind,
  sourcePathOf(sink) as source_path
