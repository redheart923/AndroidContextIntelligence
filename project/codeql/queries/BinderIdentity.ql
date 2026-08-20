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

predicate pairedAllExits(Call clear, Call restore) {
  isIdentityClear(clear) and isIdentityRestore(restore) and
  clear.getCaller() = restore.getCaller() and
  dominates(clear.getControlFlowNode(), restore.getControlFlowNode()) and
  postDominates(restore.getControlFlowNode(), clear.getControlFlowNode())
}

predicate identityRow(Call clear, int restoreLine, string resultStatus) {
  exists(Call restore |
    pairedAllExits(clear, restore) and restoreLine = restore.getLocation().getStartLine() and
    resultStatus = "paired_all_exits"
  )
  or
  isIdentityClear(clear) and not exists(Call restore | pairedAllExits(clear, restore)) and
  restoreLine = 0 and resultStatus = "missing_all_exit_restore"
}

from Call clear, int restoreLine, string resultStatus
where
  identityRow(clear, restoreLine, resultStatus)
select
  schemaVersion() as schema_version,
  symbolKey(clear.getCaller()) as owner_symbol_key,
  clear.getLocation().getStartLine() as clear_line,
  restoreLine as restore_line,
  resultStatus as transition_status,
  sourcePathOf(clear) as source_path
