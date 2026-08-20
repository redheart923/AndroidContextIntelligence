/**
 * @name Android system-service argument dataflow paths
 * @description Exports CodeQL path explanations from Binder entry parameters to configured sensitive sinks.
 * @kind path-problem
 * @problem.severity warning
 * @precision high
 * @id android-context/system-service-dataflow-path
 */

import java
import semmle.code.java.dataflow.DataFlow
import lib.ExportHelpers
import lib.SystemServiceModels

module SystemServicePathConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(Parameter parameter |
      isBinderEntry(parameter.getCallable()) and source.asParameter() = parameter
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(Call call | isConfiguredSink(call) and sink.asExpr() = call.getAnArgument())
  }
}

module SystemServicePath = DataFlow::Global<SystemServicePathConfig>;

import SystemServicePath::PathGraph

from SystemServicePath::PathNode source, SystemServicePath::PathNode sink,
  Parameter parameter, Call sinkCall
where
  SystemServicePath::flowPath(source, sink) and
  source.getNode().asParameter() = parameter and
  sink.getNode().asExpr() = sinkCall.getAnArgument() and
  isConfiguredSink(sinkCall)
select
  sink.getNode(), source, sink,
  "ACI1;scenario=binder_argument_to_sensitive_sink;entry=" +
    symbolKey(parameter.getCallable()) + ";source_parameter_index=" +
    parameter.getPosition().toString() + ";source_repository=" +
    repositoryPathOf(parameter) + ";source_path=" + sourcePathOf(parameter) +
    ";sink_owner=" +
    symbolKey(sinkCall.getCaller()) + ";sink_callable=" +
    sinkCall.getCallee().getQualifiedName() + ";sink_repository=" +
    repositoryPathOf(sinkCall) + ";sink_path=" + sourcePathOf(sinkCall)
