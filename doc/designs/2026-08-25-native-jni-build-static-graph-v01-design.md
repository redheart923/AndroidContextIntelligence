# Native / JNI / Build Static Graph v0.1 设计

状态：已确认，待实施计划

日期：2026-08-25

适用项目：Android Context Intelligence

## 1. 目标

在没有完整、可构建 AOSP 的条件下，为 Android Context Intelligence 增加可重复验证的 C、C++、Rust、JNI、Soong Blueprint 和 Ninja 静态事实层。该能力必须沿用现有多仓库 execution plan、稳定符号身份、provenance、correction、coverage validation 和原子发布机制。

本阶段交付正式可查询的静态基础图：

- C/C++/Rust 符号、类型、包含或导入关系；
- Rust trait、impl 和 C ABI/FFI 边界；
- Java/Kotlin native 声明与 C/C++/Rust 实现之间的确定性 JNI 绑定；
- Android.bp 模块、defaults、filegroup、genrule、源码归属和模块依赖；
- 对已有 Ninja 和构建元数据的可选导入；
- 对无法确定的引用、条件和绑定建立隔离候选与诊断，而不是伪造确定关系。

## 2. 明确不做

v0.1 不实现或不宣称支持：

- 精确 native call graph；
- 精确 native interprocedural dataflow；
- C/C++ 宏展开后的完整语义；
- Rust 宏展开后的完整语义；
- 完整模拟 Soong 产品配置、插件和所有条件求值；
- 主动运行 Soong、Ninja、Cargo 或 Clang 生成构建产物；
- Native Binder、HIDL 或 AIDL NDK 端到端图；
- 完整 AOSP real-source acceptance。

这些能力分别留给后续 Clang、CodeQL C/C++、SCIP、Joern、rust-analyzer、Soong 和运行时适配器。Ctags 仍是确定性宽覆盖基础图，不会被精度适配器替代。

## 3. 已确认决策

1. Soong 解析确定性模块、变量、defaults、filegroup、genrule 和依赖；条件与缺失依赖保留 unresolved。
2. JNI 同时覆盖命名式 JNI、`JNINativeMethod`/`RegisterNatives` 显式注册，以及 Java/Kotlin native 声明。
3. Rust 覆盖符号、`mod`、`use`、trait、impl、Android.bp 依赖和 `extern "C"`/导出属性；不推断内部精确调用图。
4. 可选消费 `build.ninja`、`compile_commands.json`、`rust-project.json` 和 `module-info.json`，但不负责生成。
5. Ctags 提供宽覆盖符号，Tree-sitter 提供 C/C++/Rust 结构语义，Blueprint 和 Ninja 使用专用解析器。
6. 使用语言明确的逻辑节点，并沿用 `SYMBOL_DEFINITION -> DEFINES_SYMBOL -> logical symbol`。
7. 确定事实进入有效图；不确定结果进入 `EXTRACTION_CANDIDATE` 审计层，默认查询不可见，可通过 correction 管理。
8. 当前验收采用原创多仓库 golden fixture 加 WSL partial workspace smoke；完整 AOSP 验收保持 `NOT RUN`。

## 4. 总体架构

```text
Repository discovery + language inventory
                    |
                    v
          Capability execution plan
                    |
                    v
 Ctags / Tree-sitter / Blueprint / optional build inputs
                    |
                    v
               Typed Facts
                    |
                    v
          Identity / Reference Resolver
                    |
                    v
         JNI / FFI / Build Linkers
                    |
                    v
          Staging Graph Materializer
                    |
                    v
 Validation / fingerprint / atomic publication
```

解析器不得自行发明 SQLite 语义。每个解析器先输出稳定、可序列化、可做 golden 测试的 typed fact；resolver 和 linker 负责跨文件、跨仓库和跨语言解析；materializer 是唯一的正式图写入边界。

## 5. Typed Fact 契约

公共事实类型：

```text
SymbolFact
RelationFact
BuildModuleFact
BuildActionFact
InteropBindingFact
CandidateFact
DiagnosticFact
```

每条事实至少包含：

```text
fact_kind
language
repository
source_path
source_range
logical_identity
extractor
extractor_version
evidence_kind
source_revision
content_fingerprint
properties
```

证据使用枚举而非模糊数值置信度：

```text
source_declaration
explicit_registration
naming_convention
build_declaration
generated_build_artifact
resolved_reference
ambiguous_candidate
unresolved_reference
approved_correction
```

事实编码为规范化 JSONL：字段顺序、集合排序、路径规范化和 identity 算法必须确定。相同输入、配置和工具身份必须产生相同 graph fingerprint。

## 6. 图模型

### 6.1 源码符号

逻辑节点包括：

```text
C_FUNCTION
C_TYPE
C_MACRO
CPP_FUNCTION
CPP_METHOD
CPP_TYPE
CPP_NAMESPACE
CPP_FIELD
RUST_CRATE
RUST_MODULE
RUST_FUNCTION
RUST_TYPE
RUST_TRAIT
RUST_IMPL
RUST_MACRO_INVOCATION
NATIVE_SYMBOL
```

定义身份包含 repository、source path、source range 和内容 fingerprint。逻辑身份使用语言、符号种类和 canonical qualified name。不同仓库的同名定义不会静默覆盖。

关系包括：

```text
DECLARED_IN
DEFINES_SYMBOL
HAS_METHOD
CONTAINS
EXTENDS
IMPLEMENTS_TRAIT
IMPLEMENTS_TYPE
INCLUDES
IMPORTS_SYMBOL
EXPORTS_C_ABI_SYMBOL
```

### 6.2 构建节点

```text
SOONG_MODULE
BUILD_RULE
BUILD_ACTION
BUILD_ARTIFACT
NATIVE_LIBRARY
SOURCE_SET
```

`SOONG_MODULE` 用 `module_kind` 区分 `cc_library`、`cc_binary`、`rust_library`、`rust_binary`、`java_library`、`defaults`、`filegroup`、`genrule` 等类型。

```text
SOONG_MODULE USES_DEFAULTS SOONG_MODULE
SOONG_MODULE DEPENDS_ON SOONG_MODULE
SOONG_MODULE COMPILES_SOURCE FILE
SOONG_MODULE EXPANDS_FILEGROUP SOONG_MODULE
SOONG_MODULE GENERATES BUILD_ARTIFACT
SOONG_MODULE PRODUCES NATIVE_LIBRARY
BUILD_ACTION CONSUMES FILE
BUILD_ACTION CONSUMES BUILD_ARTIFACT
BUILD_ACTION PRODUCES BUILD_ARTIFACT
BUILD_ACTION USES_RULE BUILD_RULE
SOONG_MODULE MATERIALIZED_AS BUILD_ACTION
```

条件关系不写为无条件 `DEPENDS_ON`。它们以候选或条件事实保留原始表达式、分支和解析状态。

### 6.3 JNI/FFI 节点与关系

```text
JNI_REGISTRATION
EXTRACTION_CANDIDATE
```

```text
JAVA_METHOD JNI_BINDS_TO CPP_FUNCTION|C_FUNCTION|RUST_FUNCTION
KOTLIN_METHOD JNI_BINDS_TO CPP_FUNCTION|C_FUNCTION|RUST_FUNCTION
JNI_REGISTRATION REGISTERS_JAVA_METHOD JAVA_METHOD|KOTLIN_METHOD
JNI_REGISTRATION BINDS_NATIVE_FUNCTION CPP_FUNCTION|C_FUNCTION|RUST_FUNCTION
JNI_REGISTRATION DECLARED_IN FILE
EXTRACTION_CANDIDATE HAS_CANDIDATE_TARGET LOGICAL_SYMBOL
```

所有新 edge type 都必须配置允许的端点类型并进入 validation。

## 7. 解析器组件

```text
collectors/
  facts/       model, codec, identity
  native/      ctags symbols, C/C++ syntax, Rust syntax, resolver, materializer
  build/       Blueprint, Soong resolver, Ninja and optional build-input adapters
  interop/     managed native scanner, JNI/Rust scanners, name codec, linker
```

### 7.1 C/C++

Universal Ctags 生成函数、类型、字段和宏的宽覆盖定义。Tree-sitter C/C++ 解析 namespace、class、method、继承、include、签名、JNI 表、注册调用和函数指针。Tree-sitter 失败时保留 Ctags 事实，但对应结构语义能力必须标记 degraded，不能退回正则后声称 supported。

`.c` 使用 C grammar，`.cc`、`.cpp`、`.cxx` 使用 C++ grammar。`.h`、`.hh`、`.hpp` 的语言身份优先来自 `compile_commands.json`，其次来自已解析的 Soong module/translation-unit include 上下文。没有唯一上下文时只保留 Ctags 定义和 `ambiguous_header_language` 诊断，不生成依赖 C/C++ grammar 选择的确定关系。

### 7.2 Rust

Tree-sitter Rust 提取 crate、mod、use、struct、enum、trait、type、impl、function、`extern "C"`、`no_mangle`、`export_name` 和 `link_name`。宏调用保留为未展开事实。存在 `rust-project.json` 时才增强 crate root、edition、cfg、依赖和生成源码信息。

### 7.3 Blueprint/Soong

Blueprint 使用 tokenizer 加递归下降解析器，不能使用正则处理嵌套 list/map。解析流程：

1. 解析模块、变量、属性和源码范围；
2. 建立模块名、namespace、defaults 和 filegroup 索引；
3. 求值字符串、布尔值、list/map、简单变量、`+=`、列表拼接、defaults 和 filegroup；
4. 对 `select`、`soong_config_variables`、`product_variables`、arch/target 条件、缺失 namespace 或插件语义保留 unresolved。

模块同时保留 `raw_properties`、`resolved_properties` 和 `unresolved_expressions`。

### 7.4 Ninja 与可选构建输入

Ninja parser 支持 variable、rule、build、phony、default、include、subninja、pool、多行转义、隐式输入和 order-only 依赖。只解析已有文件。

可选适配器：

- `compile_commands.json`：translation unit、编译器、include、define 和语言标准；
- `rust-project.json`：crate、dependency、cfg、edition 和 generated source；
- `module-info.json`：Soong 模块路径、class、installed files 和依赖补充。

完整 command line 默认不写图。只保存允许列出的结构化参数、规范化路径、command hash 和输入文件 hash，避免泄漏凭据与环境变量。

## 8. JNI/FFI 链接规则

托管侧解析 Java `native` 和 Kotlin `external fun`。Native 侧覆盖 JNI 短/长名称、重载签名、`JNINativeMethod`、`RegisterNatives`、`jniRegisterNativeMethods`、`AndroidRuntime::registerNativeMethods`、C ABI 和 Rust 导出。

JNI 名称解码必须实现 `_1`、`_2`、`_3`、`_0xxxx` 和重载签名规则。链接优先级：

1. 显式 class + method + descriptor 注册；
2. 标准 JNI 长名称；
3. 标准 JNI 短名称，且托管候选唯一；
4. 构建模块和 native library 仅用于消歧，不单独证明绑定；
5. 多候选、descriptor 冲突或缺失一侧源码时生成候选。

确定绑定必须能反向查询托管声明、Native 实现、证据源码位置、解析器版本和构建批次。

## 9. 候选与纠错

不确定结果以 `EXTRACTION_CANDIDATE` 保存，不进入默认有效语义视图。候选包含 kind、status、候选端点、源码证据、解析原因和 revision。

现有 file-backed correction 扩展为：

```text
promote_candidate
suppress_candidate
replace_binding
annotate
```

Correction 必须绑定源码 revision 或内容 fingerprint；不匹配时为 stale。Promotion 仍需通过端点、descriptor、identity 和 scope 校验。原始事实与候选不可删除，有效视图叠加 correction。

## 10. 能力与状态

新增能力：

```text
native_symbols
native_types
native_includes
rust_ffi
jni_bindings
soong_build_graph
ninja_build_graph
native_build_membership
```

以下能力保持 unsupported：

```text
native_call_graph
native_interprocedural_dataflow
native_binder
```

能力只有在 parser 执行、产生对应 evidence 并通过 validation 后才为 supported。安装了解析库或仅检测到文件不能算 supported。non-strict 构建可发布其他已验证层并把失败能力标为 degraded；strict capability 失败时保留旧 live DB。

## 11. 配置与命令

仓库继续由 `source_roots.default.toml` 和 `source_roots.local.toml` 控制。可选构建产物放在独立 `build_inputs.local.toml`：

```toml
[[inputs]]
kind = "ninja"
path = "/home/ts/artifacts/out/soong/build.ninja"
optional = true

[[inputs]]
kind = "compile_commands"
path = "/home/ts/artifacts/compile_commands.json"
repository = "frameworks/base"
optional = true
```

规范入口：

```bash
bash scripts/rebuild_all.sh
bash scripts/rebuild_all.sh --build-inputs config/build_inputs.local.toml
bash scripts/rebuild_all.sh --strict-capability native_symbols
bash scripts/rebuild_all.sh --strict-capability jni_bindings
bash scripts/rebuild_all.sh --plan-only
```

`--strict-capability` 改为可重复参数并保持单次调用兼容。安装和 WSL 部署继续使用根目录 `setup.sh`；不新增永久性的 patch/install shell 作为唯一源码。

## 12. 验证门禁

### 12.1 通用门禁

- typed fact schema、source range 和 fingerprint；
- stable identity 与 deterministic output；
- foreign key、端点类型、collision 和 source scope；
- provenance 完整；
- candidate 不进入默认有效视图；
- parser 失败不得发布 supported；
- graph fingerprint 可重复；
- staging 失败保留旧 live DB。

### 12.2 JNI 门禁

有效 `JNI_BINDS_TO` 必须来自显式注册、可逆的标准 JNI 名称、唯一短名称或有效 correction。显式注册需验证托管类、方法、descriptor、native function、registration array 和注册调用关联。重载短名称、descriptor 不匹配、复杂宏和多候选只能形成候选。

### 12.3 Soong/Ninja 门禁

模块身份在 namespace 内唯一；确定性依赖两端存在；缺失模块不得形成 active edge；条件属性不得伪装为无条件关系；defaults/filegroup 循环可诊断。Ninja 需区分显式、隐式、order-only 和 phony；冲突 output producer 不得作为确定事实发布。

### 12.4 Rust/FFI 门禁

验证 impl target、ABI、导出属性冲突、重复 exporter、crate root 和 cfg 状态。未知 cfg、未展开宏和缺失生成源码必须形成 coverage gap。

## 13. 测试与验收

建立原创最小多仓库 fixture，覆盖 Java native、Kotlin external、命名式与显式 JNI、C++ 类型/include、Rust trait/impl/FFI、Android.bp、Ninja 和所有可选构建输入。

负例包括错误 descriptor、缺失类、重复 exporter、条件依赖、缺失模块、defaults 循环、Ninja output 冲突、未知 cfg、stale correction 和 parser failure。

验收分层：

1. fixture：unit、golden facts、golden graph、negative cases、migration、FK、fingerprint、rollback；
2. WSL partial workspace：库存、coverage、候选隔离、缺失可选输入和原子发布；
3. real AOSP：保持 `NOT RUN`，以后验证 Framework JNI、SurfaceFlinger、Rust Android 模块和 Soong-to-Ninja 链路。

Fixture PASS 或 partial smoke PASS 不能描述为完整 AOSP 已验证。

## 14. 实施阶段与 Git 门禁

实施分为：

1. typed fact、adapter contract、schema、registry 和 fixture scaffold；
2. C/C++/Rust 静态符号、类型、include/import 和 FFI；
3. Blueprint/Soong 模块与依赖图；
4. Ninja 与可选构建输入；
5. JNI/FFI linker、candidate、correction 和整体门禁。

每阶段在独立 `codex/` 分支与 worktree 中按 TDD 实施，形成独立 commit，review 和验证后合并 `main`。只有已合并且干净的物理 worktree 才清理。规范 Python 源码、配置、migration、fixture、查询和文档进入 Git；生成数据库、缓存和本机 local 配置不提交。

## 15. 后续扩展

v0.1 稳定后，精度增强按独立里程碑接入：

- Clang/SCIP/CodeQL C++ 精确调用与数据流；
- Rust 语义精度适配器；
- Native Binder/HIDL/AIDL NDK；
- 完整 Soong/Ninja real-AOSP acceptance；
- ADB/runtime JNI 和 native service 观测。

这些适配器写入相同 typed fact 和 provenance 边界，不替换已验证的基础事实层。
