/**
 * @name Android Binder identity transitions
 * @description Exports clear/restore pairs and diagnostics for clears not restored on all exits.
 * @kind table
 * @id android-context/binder-identity
 */

import java
import semmle.code.java.controlflow.Dominance
import lib.ExportHelpers
import lib.SystemServiceModels

predicate restoresSameToken(Call clear, Call restore) {
  exists(LocalVariableDecl token, VarAccess access |
    token.getInitializer() = clear and
    restore.getArgument(0) = access and
    access.getVariable() = token
  )
}

predicate restoreIsInFinally(Call restore) {
  exists(TryStmt guarded |
    guarded.getFinally() = restore.getEnclosingStmt().getEnclosingStmt*()
  )
}

predicate pairedAllExits(Call clear, Call restore) {
  isIdentityClear(clear) and isIdentityRestore(restore) and
  clear.getCaller() = restore.getCaller() and
  restoresSameToken(clear, restore) and
  restoreIsInFinally(restore) and
  dominates(clear.getControlFlowNode(), restore.getControlFlowNode()) and
  postDominates(restore.getControlFlowNode(), clear.getControlFlowNode())
}

predicate sinkInsideIdentityRegion(Call clear, Call restore, Call sink) {
  pairedAllExits(clear, restore) and isConfiguredSink(sink) and
  sink.getCaller() = clear.getCaller() and
  dominates(clear.getControlFlowNode(), sink.getControlFlowNode()) and
  postDominates(restore.getControlFlowNode(), sink.getControlFlowNode())
}

predicate identityRow(Call clear, Call sink, int restoreLine, string resultStatus) {
  exists(Call restore |
    sinkInsideIdentityRegion(clear, restore, sink) and
    restoreLine = restore.getLocation().getStartLine() and
    resultStatus = "paired_all_exits"
  )
  or
  isIdentityClear(clear) and not exists(Call restore | pairedAllExits(clear, restore)) and
  isConfiguredSink(sink) and sink.getCaller() = clear.getCaller() and
  dominates(clear.getControlFlowNode(), sink.getControlFlowNode()) and
  restoreLine = 0 and resultStatus = "missing_all_exit_restore"
}

from Call clear, Call sink, int restoreLine, string resultStatus
where
  identityRow(clear, sink, restoreLine, resultStatus)
select
  schemaVersion() as schema_version,
  symbolKey(clear.getCaller()) as owner_symbol_key,
  clear.getLocation().getStartLine() as clear_line,
  restoreLine as restore_line,
  sink.getLocation().getStartLine() as sink_line,
  resultStatus as transition_status,
  sourcePathOf(clear) as source_path
