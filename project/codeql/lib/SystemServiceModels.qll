import java

predicate callTo(Call call, string packageName, string typeName, string methodName) {
  call.getCallee().hasQualifiedName(packageName, typeName, methodName)
}

predicate isCallerIdentitySource(Call call) {
  callTo(call, "android.os", "Binder", "getCallingUid") or
  callTo(call, "android.os", "Binder", "getCallingPid")
}

predicate isPermissionCheck(Call call) {
  callTo(call, "android.content", "Context", "enforceCallingPermission") or
  callTo(call, "android.content", "Context", "enforceCallingOrSelfPermission")
}

predicate isPermissionResultCheck(Call call) {
  callTo(call, "android.content", "Context", "checkCallingPermission") or
  callTo(call, "android.content", "Context", "checkCallingOrSelfPermission")
}

predicate isAppOpsCheck(Call call) {
  call.getCallee().getName() = ["noteOp", "noteOpNoThrow", "checkOp", "checkOpNoThrow"] and
  call.getCallee().getDeclaringType().getQualifiedName().regexpMatch("android\\.app\\..*AppOps.*")
}

predicate isCrossUserCheck(Call call) {
  call.getCallee().getName() = "handleIncomingUser" and
  call.getCallee().getDeclaringType().getQualifiedName().regexpMatch("android\\.app\\..*")
}

predicate isIdentityClear(Call call) {
  callTo(call, "android.os", "Binder", "clearCallingIdentity")
}

predicate isIdentityRestore(Call call) {
  callTo(call, "android.os", "Binder", "restoreCallingIdentity")
}

predicate isConfiguredGuard(Call call) {
  // Return-code checks and user-resolution calls require branch-sensitive
  // success/failure modelling before they can be claimed as guards.
  isPermissionCheck(call)
}

predicate isConfiguredSink(Call call) {
  callTo(call, "fixture.security", "SensitiveStore", "writeSecureSetting") or
  callTo(call, "com.android.server.pm", "PackageManagerService", "deletePackage") or
  callTo(call, "com.android.server.pm", "DeletePackageHelper", "deletePackageX")
}

predicate isBinderEntry(Callable callable) {
  callable instanceof Method and callable.isPublic() and
  callable.getDeclaringType().getASupertype*().hasQualifiedName("android.os", "Binder")
}
