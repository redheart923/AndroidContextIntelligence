/**
 * @name Android Context callable definitions and call targets
 * @description Exports source-backed Java/Kotlin definitions and explicit call-target evidence.
 * @kind table
 * @id android-context/call-sites
 */

import java
import lib.ExportHelpers

predicate acceptedTarget(Call call, Callable target) {
  sourceBacked(target) and
  (
    not call instanceof VirtualMethodCall and target = call.getCallee()
    or
    call instanceof VirtualMethodCall and
    target = call.getCallee().(Method).getAPossibleImplementation()
  )
}

int candidateCount(Call call) { result = count(Callable target | acceptedTarget(call, target)) }

string dispatchKind(Call call) {
  call instanceof ConstructorCall and result = "constructor"
  or call instanceof StaticMethodCall and result = "static"
  or call instanceof SuperMethodCall and result = "super"
  or call instanceof VirtualMethodCall and result = "virtual"
  or
  not call instanceof ConstructorCall and
  not call instanceof StaticMethodCall and
  not call instanceof SuperMethodCall and
  not call instanceof VirtualMethodCall and result = "direct"
}

predicate staticallyUnique(Call call) {
  not call instanceof VirtualMethodCall
  or
  call.getCallee() instanceof Method and
  (call.getCallee().(Method).isPrivate() or call.getCallee().(Method).isFinal() or
    call.getCallee().getDeclaringType().isFinal())
}

string relationKind(Call call) {
  candidateCount(call) = 0 and result = "unresolved"
  or candidateCount(call) = 1 and staticallyUnique(call) and result = "must"
  or candidateCount(call) > 0 and not staticallyUnique(call) and result = "may"
  or candidateCount(call) > 1 and not staticallyUnique(call) and result = "may"
}

predicate definitionRow(
  Element location, string recordKind, string languageName, string packageName,
  string declaringType, string callableKind, string callableName,
  string erasedParameters, string returnType, string callerKey,
  string calleeKey, string dispatch, string relation, int candidates,
  string expressionText, string unresolvedReason
) {
  exists(Callable callable |
    sourceBacked(callable) and location = callable and recordKind = "definition" and
    languageName = languageOf(callable) and packageName = packageOf(callable) and
    declaringType = declaringTypeOf(callable) and callableKind = callableKindOf(callable) and
    callableName = callable.getName() and erasedParameters = erasedParametersOf(callable) and
    returnType = returnTypeOf(callable) and callerKey = symbolKey(callable) and calleeKey = "" and
    dispatch = "" and relation = "" and candidates = 0 and expressionText = callable.toString() and
    unresolvedReason = ""
  )
}

predicate callRow(
  Element location, string recordKind, string languageName, string packageName,
  string declaringType, string callableKind, string callableName,
  string erasedParameters, string returnType, string callerKey,
  string calleeKey, string dispatch, string relation, int candidates,
  string expressionText, string unresolvedReason
) {
  exists(Call call, Callable caller |
    sourceBacked(call) and caller = call.getCaller() and location = call and recordKind = "call" and
    languageName = languageOf(call) and packageName = packageOf(caller) and
    declaringType = declaringTypeOf(caller) and callableKind = callableKindOf(caller) and
    callableName = caller.getName() and erasedParameters = erasedParametersOf(caller) and
    returnType = returnTypeOf(caller) and callerKey = symbolKey(caller) and
    dispatch = dispatchKind(call) and relation = relationKind(call) and
    candidates = candidateCount(call) and expressionText = call.toString() and
    (
      exists(Callable target | acceptedTarget(call, target) |
        calleeKey = symbolKey(target) and unresolvedReason = ""
      )
      or
      not exists(Callable target | acceptedTarget(call, target)) and calleeKey = "" and
      unresolvedReason = "no_source_backed_target"
    )
  )
}

from
  Element location, string recordKind, string languageName, string packageName,
  string declaringType, string callableKind, string callableName,
  string erasedParameters, string returnType, string callerKey,
  string calleeKey, string dispatch, string relation, int candidates,
  string expressionText, string unresolvedReason
where
  definitionRow(location, recordKind, languageName, packageName, declaringType, callableKind,
    callableName, erasedParameters, returnType, callerKey, calleeKey, dispatch, relation,
    candidates, expressionText, unresolvedReason)
  or
  callRow(location, recordKind, languageName, packageName, declaringType, callableKind,
    callableName, erasedParameters, returnType, callerKey, calleeKey, dispatch, relation,
    candidates, expressionText, unresolvedReason)
select
  schemaVersion() as schema_version,
  recordKind as record_kind,
  languageName as language,
  packageName as package_name,
  declaringType as declaring_type,
  callableKind as callable_kind,
  callableName as callable_name,
  erasedParameters as erased_parameters,
  returnType as return_type,
  repositoryPathOf(location) as repository_path,
  sourcePathOf(location) as source_path,
  location.getLocation().getStartLine() as start_line,
  location.getLocation().getStartColumn() as start_column,
  location.getLocation().getEndLine() as end_line,
  location.getLocation().getEndColumn() as end_column,
  callerKey as caller_symbol_key,
  calleeKey as callee_symbol_key,
  dispatch as dispatch_kind,
  relation as relation_kind,
  candidates as candidate_count,
  expressionText as expression_text,
  unresolvedReason as unresolved_reason
