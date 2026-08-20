import java

string schemaVersion() { result = "1" }

string languageOf(Element e) {
  e.getFile().isKotlinSourceFile() and result = "kotlin"
  or
  e.getFile().isJavaSourceFile() and result = "java"
}

string packageOf(Callable c) { result = c.getDeclaringType().getPackage().getName() }

string declaringTypeOf(Callable c) {
  c.getDeclaringType() instanceof AnonymousClass and
  result = c.getDeclaringType().getPackage().getName() + ".<anonymous>@" +
    c.getDeclaringType().getFile().getRelativePath() + ":" +
    c.getDeclaringType().getLocation().getStartLine().toString() + ":" +
    c.getDeclaringType().getLocation().getStartColumn().toString()
  or
  not c.getDeclaringType() instanceof AnonymousClass and
  result = c.getDeclaringType().getQualifiedName()
}

string callableKindOf(Callable c) {
  c instanceof Constructor and result = "constructor"
  or
  c instanceof Method and result = "method"
}

string erasedTypeName(Type type) {
  type.getErasure() instanceof RefType and
  result = type.getErasure().(RefType).getQualifiedName()
  or
  not type.getErasure() instanceof RefType and result = type.getErasure().toString()
}

string erasedParametersOf(Callable c) {
  result = concat(int i |
    i = [0 .. c.getNumberOfParameters() - 1] |
    erasedTypeName(c.getParameterType(i)), "," order by i
  )
}

string returnTypeOf(Callable c) { result = erasedTypeName(c.getReturnType()) }

string symbolKey(Callable c) {
  result = languageOf(c) + "|" + callableKindOf(c) + "|" + declaringTypeOf(c) +
    "#" + c.getName() + "(" + erasedParametersOf(c) + ")"
}

string repositoryPathOf(Element e) {
  result = e.getFile().getRelativePath().regexpCapture("([^/]+/[^/]+).*", 1)
  or
  not e.getFile().getRelativePath().regexpMatch("[^/]+/[^/]+/.*") and result = ""
}

string sourcePathOf(Element e) { result = e.getFile().getRelativePath() }

predicate sourceBacked(Element e) {
  e.fromSource() and (e.getFile().isJavaSourceFile() or e.getFile().isKotlinSourceFile())
}
