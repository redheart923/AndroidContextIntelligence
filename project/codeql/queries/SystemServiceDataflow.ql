/**
 * @name Android system-service argument dataflow
 * @description Exports global value flow from Binder entry parameters to configured sensitive sinks.
 * @kind table
 * @id android-context/system-service-dataflow
 */

import java
import semmle.code.java.dataflow.DataFlow
import lib.ExportHelpers
import lib.SystemServiceModels

module SystemServiceFlowConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(Parameter parameter |
      isBinderEntry(parameter.getCallable()) and source.asParameter() = parameter
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(Call call | isConfiguredSink(call) and sink.asExpr() = call.getAnArgument())
  }
}

module SystemServiceFlow = DataFlow::Global<SystemServiceFlowConfig>;

from DataFlow::Node source, DataFlow::Node sink, Parameter parameter, Call sinkCall
where
  SystemServiceFlow::flow(source, sink) and source.asParameter() = parameter and
  sink.asExpr() = sinkCall.getAnArgument() and isConfiguredSink(sinkCall)
select
  schemaVersion() as schema_version,
  "binder_argument_to_sensitive_sink" as scenario,
  symbolKey(parameter.getCallable()) as entry_symbol_key,
  parameter.getPosition() as source_parameter_index,
  parameter.getName() as source_value,
  symbolKey(sinkCall.getCaller()) as sink_owner_symbol_key,
  sinkCall.getCallee().getQualifiedName() as sink_callable,
  sinkCall.getLocation().getFile().getRelativePath() as source_path,
  sinkCall.getLocation().getStartLine() as sink_line,
  source.toString() as source_identity,
  sink.toString() as sink_identity
