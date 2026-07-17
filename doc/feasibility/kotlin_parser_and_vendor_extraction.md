# Kotlin 解析器技术选型 & userdebug 厂商定制提取方案

## 一、Kotlin 解析器：4 个可选方案

### 方案对比

| 维度 | ① Universal Ctags | ② Tree-sitter-kotlin | ③ jadx 反编译 | ④ kotlin-compiler-embeddable |
|---|---|---|---|---|
| **实现成本** | ⭐ 极低 | ⭐⭐ 低 | ⭐⭐ 低 | ⭐⭐⭐⭐ 高 |
| **能力** | 符号 + 继承 + 包结构 | AST 全节点 | 完整 Java 源码 | 完整语义（类型推断） |
| **新增依赖** | 无（已有 Ctags） | `tree-sitter` + grammar | `jadx-cli`（Java 工具） | JVM + `kotlin-compiler.jar` |
| **与现有代码兼容** | ✅ 直接复用 `ctags_importer.py` | 需新写 collector | 反编译后复用现有 Java collector | 需写 JVM→Python 桥接 |
| **Kotlin 特有语法** | 部分（data class、object、companion 有） | 全部 | 转为 Java 后丢失 | 全部 |
| **推荐用途** | v0.2 快速落地 | v0.3 精确 AST | 厂商产物分析 | v1.0 完整语义 |

---

### 方案 ①：Universal Ctags（推荐 v0.2 首选）

Universal Ctags **已内置 Kotlin 解析器**，无需额外安装。它支持以下 kinds：

```text
Kind     Description
─────    ──────────────────
c        class
i        interface
o        object (包括 companion object)
T        typealias
m        method / function
p        property (val/var)
C        constant (const val)
v        variable
```

#### 关键能力验证

```bash
# 确认已有 Kotlin 支持
ctags --list-languages | grep -i kotlin

# 查看支持的 kinds
ctags --list-kinds-full=Kotlin

# 测试输出 JSON（与 Java 相同的格式）
ctags --output-format=json --fields=+nKSEi -R --languages=Kotlin /path/to/kotlin/src
```

#### 与现有代码的集成点

现有 [ctags_importer.py](file:///D:/AndroidContextIntelligence/android-context-current/collectors/source/ctags_importer.py) 的架构已经为 Kotlin 做好了扩展准备：

```python
# 当前的 KIND_MAP（Java only）
KIND_MAP = {
    "class": "JAVA_CLASS",
    "interface": "JAVA_INTERFACE",
    "method": "JAVA_METHOD",
    ...
}
```

扩展方式：**添加 Kotlin KIND_MAP 或统一为语言无关的 KIND_MAP**

```python
# 方案 A：独立 Kotlin 节点类型（保留区分）
KOTLIN_KIND_MAP = {
    "class": "KOTLIN_CLASS",
    "interface": "KOTLIN_INTERFACE",
    "object": "KOTLIN_OBJECT",
    "typealias": "KOTLIN_TYPEALIAS",
    "method": "KOTLIN_METHOD",
    "property": "KOTLIN_PROPERTY",
    "constant": "KOTLIN_CONSTANT",
    "variable": "KOTLIN_VARIABLE",
}

# 方案 B：统一 JVM 节点类型（推荐，因为 Kotlin class 编译后就是 Java class）
UNIFIED_KIND_MAP = {
    "class": "JVM_CLASS",       # Java class + Kotlin class + data class
    "interface": "JVM_INTERFACE",
    "object": "KOTLIN_OBJECT",   # Kotlin 特有
    "typealias": "KOTLIN_TYPEALIAS",  # Kotlin 特有
    "method": "JVM_METHOD",
    "property": "KOTLIN_PROPERTY",
    "field": "JVM_FIELD",
    ...
}
```

> [!TIP]
> **推荐方案 B**。因为 CTS/XTS 根因分析和 Binder 链路追踪时，Kotlin class 和 Java class 是等价的 JVM 类型。区分语言对图谱查询没有实际意义。Kotlin 特有的 `object` 和 `typealias` 单独保留即可。

#### 需要改动的文件

| 文件 | 改动 |
|---|---|
| [ctags_importer.py](file:///D:/AndroidContextIntelligence/android-context-current/collectors/source/ctags_importer.py) | 添加 Kotlin KIND_MAP，修改 `PACKAGE_RE` 支持无分号的 `package` 声明 |
| [java_inheritance_importer.py](file:///D:/AndroidContextIntelligence/android-context-current/collectors/source/java_inheritance_importer.py) | 支持 Kotlin 的 `: SuperClass, Interface` 继承语法（Ctags 的 `inherits` 字段已包含） |
| [parser_registry.toml](file:///D:/AndroidContextIntelligence/android-context-current/config/parser_registry.toml) | 启用 Kotlin 解析器 |
| `scripts/rebuild_all.sh` | 添加 Kotlin Ctags 扫描步骤 |

#### Kotlin `package` 声明差异

当前 Java 的 package 正则：
```python
PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;", re.MULTILINE)
```

Kotlin **没有分号**，需要适配：
```python
# 同时兼容 Java（带分号）和 Kotlin（不带分号）
PACKAGE_RE = re.compile(
    r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;?",
    re.MULTILINE,
)
```

#### 预估工作量

```text
改动 ctags_importer.py：         ~2 小时
改动 java_inheritance_importer：  ~1 小时
更新 parser_registry.toml：       ~10 分钟
更新 rebuild_all.sh：             ~30 分钟
写测试：                          ~2 小时
────────────────────────────────
合计：                            ~1 天
```

---

### 方案 ②：Tree-sitter-kotlin

适用于需要**精确 AST 级别**解析的场景（如 Kotlin 协程、扩展函数、DSL 分析）。

```bash
pip install tree-sitter tree-sitter-kotlin
```

```python
import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Parser

parser = Parser(Language(tskotlin.language()))
tree = parser.parse(source_bytes)

# 遍历 AST 提取类/方法/属性
for node in tree.root_node.children:
    if node.type == "class_declaration":
        name_node = node.child_by_field_name("name")
        ...
```

**优势**：能解析 Kotlin 特有语法（`suspend fun`、`by lazy`、`companion object`、`sealed class`、inline/value class 等）。

**劣势**：需要自己写从 AST 到图谱节点的映射，不能直接复用 Ctags importer。

**建议**：作为 v0.3 的补充层，对 Ctags 提取不了的 Kotlin 语义做增强。

---

### 方案 ③：jadx 反编译（厂商产物专用，详见第二部分）

Kotlin 编译后是标准 `.dex` / `.class` → jadx 反编译为 Java 源码 → 直接走现有 Java pipeline。

这个方案同时解决 Kotlin 和厂商定制问题，后面详述。

---

### 方案 ④：kotlin-compiler-embeddable

最高精度方案，能获得完整类型信息（包括类型推断后的类型），但需要 JVM 环境和构建上下文。

```text
适用场景：需要精确 data flow / call graph / 类型解析
不适合场景：MVP 快速落地
建议时机：v1.0 或接入 CodeQL Kotlin 时
```

---

### 推荐路径

```text
v0.2 → Universal Ctags Kotlin（1天）
             ↓
v0.3 → Tree-sitter-kotlin 补充 Kotlin 特有语义
             ↓
v0.4 → jadx 反编译层（同时服务厂商产物）
             ↓
v1.0 → CodeQL Kotlin 或 kotlin-compiler-embeddable
```

---

## 二、userdebug 设备厂商定制提取方案

你有 userdebug 设备，**这是最优的非源码分析条件**。userdebug 意味着 adb root 可用、文件系统可读、debuggable。

### 2.1 完整提取 Pipeline

```text
userdebug 设备
     ↓
① adb pull 产物
     ↓
② 产物分类 (framework / vendor / app / apex)
     ↓
③ DEX/JAR/APK → jadx 反编译 → Java 伪源码
     ↓
④ Ctags 扫描 → JSONL
     ↓
⑤ 现有 ctags_importer.py → SQLite
     ↓
⑥ 与 AOSP 基线图谱 diff → 厂商增量
```

### 2.2 第①步：从设备提取产物

```bash
# 确保 root
adb root
adb remount   # userdebug 可用

# ===== Framework 核心 JAR =====
mkdir -p vendor_extract/framework
adb pull /system/framework/framework.jar       vendor_extract/framework/
adb pull /system/framework/services.jar         vendor_extract/framework/
adb pull /system/framework/ext.jar              vendor_extract/framework/
adb pull /system/framework/telephony-common.jar vendor_extract/framework/
adb pull /system/framework/ims-common.jar       vendor_extract/framework/

# 如果是 ART 优化后的 (Android 10+)，实际 DEX 在 .vdex 或 boot image 中：
adb pull /system/framework/oat/                 vendor_extract/framework/oat/
adb pull /system/framework/boot.vdex            vendor_extract/framework/
adb pull /system/framework/boot-framework.vdex  vendor_extract/framework/

# ===== 厂商 Framework 扩展 =====
adb pull /system_ext/framework/                 vendor_extract/system_ext_fw/
adb pull /vendor/framework/                     vendor_extract/vendor_fw/
adb pull /product/framework/                    vendor_extract/product_fw/

# ===== System Apps =====
adb pull /system/priv-app/                      vendor_extract/priv_app/
adb pull /system_ext/priv-app/                  vendor_extract/system_ext_app/
adb pull /vendor/app/                           vendor_extract/vendor_app/

# ===== APEX (Mainline Modules) =====
adb pull /apex/                                 vendor_extract/apex/

# ===== 运行态信息 =====
adb shell service list                          > vendor_extract/runtime/service_list.txt
adb shell dumpsys package                       > vendor_extract/runtime/dumpsys_package.txt
adb shell dumpsys activity services             > vendor_extract/runtime/dumpsys_activity_services.txt
adb shell cmd permission list-permissions -g    > vendor_extract/runtime/permissions.txt
adb shell pm list packages -f                   > vendor_extract/runtime/package_paths.txt
adb shell lshal                                 > vendor_extract/runtime/lshal.txt
adb shell getprop                               > vendor_extract/runtime/properties.txt

# ===== 权限配置 =====
adb pull /system/etc/permissions/               vendor_extract/permissions/system/
adb pull /vendor/etc/permissions/               vendor_extract/permissions/vendor/
adb pull /product/etc/permissions/              vendor_extract/permissions/product/
adb pull /system/etc/sysconfig/                 vendor_extract/sysconfig/
```

> [!IMPORTANT]
> **Android 10+ (ART 预编译)**：设备上的 `.jar` 文件可能只包含资源而 DEX 被提取到 `.vdex` 文件中。需要用 `vdexExtractor` 或 `oatdump` 先提取出 DEX，再用 jadx 反编译。
> 
> ```bash
> # 检查 jar 中是否包含 classes.dex
> unzip -l framework.jar | grep classes.dex
> # 如果没有 → 需要从 vdex/odex 提取
> ```

### 2.3 第②③步：反编译产物

**jadx** 是推荐工具，支持 `.dex` / `.jar` / `.apk` / `.aab` / `.vdex`：

```bash
# 安装 jadx（需要 Java 11+）
# 从 https://github.com/skylot/jadx/releases 下载

# 反编译 framework.jar → Java 伪源码
jadx \
  --output-dir vendor_extract/decompiled/framework \
  --no-res \
  --show-bad-code \
  --deobf \
  --deobf-min 2 \
  vendor_extract/framework/framework.jar

# 反编译 services.jar
jadx \
  --output-dir vendor_extract/decompiled/services \
  --no-res \
  --show-bad-code \
  vendor_extract/framework/services.jar

# 批量反编译 vendor framework 扩展
for jar in vendor_extract/vendor_fw/*.jar; do
  name=$(basename "$jar" .jar)
  jadx \
    --output-dir "vendor_extract/decompiled/vendor_fw/$name" \
    --no-res \
    --show-bad-code \
    "$jar"
done

# 反编译 APK（系统应用）
for apk in vendor_extract/priv_app/*/*.apk; do
  name=$(basename "$(dirname "$apk")")
  jadx \
    --output-dir "vendor_extract/decompiled/priv_app/$name" \
    --no-res \
    --show-bad-code \
    "$apk"
done
```

#### jadx 关键参数说明

| 参数 | 说明 |
|---|---|
| `--no-res` | 不反编译资源文件，只提取代码（大幅加速） |
| `--show-bad-code` | 反编译失败的方法仍保留为注释（不丢失符号） |
| `--deobf` | 尝试去混淆 |
| `--threads-count N` | 并行线程数 |

#### 反编译后的目录结构

```text
vendor_extract/decompiled/framework/sources/
├── android/
│   ├── app/
│   │   ├── ActivityManager.java
│   │   ├── IActivityManager.java
│   │   └── ...
│   ├── content/
│   └── ...
├── com/
│   └── android/
│       └── server/
│           ├── am/
│           │   └── ActivityManagerService.java
│           └── ...
└── com/
    └── samsung/     ← 厂商新增代码
        └── android/
            └── server/
                └── SemActivityManagerService.java
```

> [!TIP]
> jadx 反编译的输出是标准 Java 源码目录结构，有 `package` 声明。**可以直接被现有的 `ctags_importer.py` 处理**，无需任何改动。

### 2.4 第④⑤步：纳入图谱

反编译后的代码等同于普通 Java 源码，直接走现有 pipeline：

```bash
# Ctags 扫描反编译产物
ctags --output-format=json \
  --fields=+nKSEi \
  -R \
  --languages=Java \
  -o vendor_extract/ctags/vendor_framework.jsonl \
  vendor_extract/decompiled/

# 导入图谱
python -m collectors.source.ctags_importer \
  vendor_extract/ctags/vendor_framework.jsonl \
  data/android_context.db \
  vendor_extract/decompiled/
```

#### TOML 配置

```toml
# config/source_roots.toml 添加厂商产物仓库

[[extra_repositories]]
name = "vendor-framework-decompiled"
path = "/home/ts/vendor_extract/decompiled/framework"
enabled = true
languages = ["java"]    # jadx 输出是 Java

[[extra_repositories]]
name = "vendor-services-decompiled"
path = "/home/ts/vendor_extract/decompiled/services"
enabled = true
languages = ["java"]

[[extra_repositories]]
name = "vendor-extensions-decompiled"
path = "/home/ts/vendor_extract/decompiled/vendor_fw"
enabled = true
languages = ["java"]
```

### 2.5 第⑥步：AOSP vs 厂商 Diff

这是整个方案最有价值的部分。有了 AOSP 基线图谱和厂商图谱后：

```sql
-- 厂商新增的 System Service（不在 AOSP 基线中）
SELECT n.qualified_name, n.source_path
FROM node n
WHERE n.node_type = 'JAVA_CLASS'
  AND n.qualified_name LIKE 'com.samsung.%'   -- 或 com.oppo.* / com.xiaomi.* 等
  AND NOT EXISTS (
    SELECT 1 FROM node aosp
    WHERE aosp.qualified_name = n.qualified_name
      AND aosp.properties_json LIKE '%"repository":"frameworks/base"%'
  );

-- 厂商新增的 Binder Service 名称
SELECT n.qualified_name
FROM node n
WHERE n.node_type = 'BINDER_SERVICE_NAME'
  AND n.qualified_name NOT IN (
    -- AOSP 已知 Service 名称列表
    'activity', 'package', 'window', 'alarm', 'power',
    'connectivity', 'wifi', 'bluetooth_manager', ...
  );

-- 运行态对比：设备上有但 AOSP 图谱中没有的 Service
-- （来自 adb shell service list）
```

### 2.6 反编译精度 vs 源码的差异

| 特征 | 源码 | jadx 反编译 |
|---|---|---|
| 类/接口/方法签名 | ✅ 精确 | ✅ 精确 |
| 继承关系 | ✅ 精确 | ✅ 精确（DEX 保留） |
| 方法体逻辑 | ✅ 精确 | ⚠️ 近似（控制流可能不同） |
| 局部变量名 | ✅ 有意义 | ❌ 通常丢失 |
| 泛型完整类型 | ✅ 精确 | ⚠️ 部分擦除 |
| Kotlin 特有语法 | ✅ 原生 | ❌ 转为 Java 等价代码 |
| 注解/Annotation | ✅ 精确 | ✅ 基本保留 |
| 包结构 | ✅ 精确 | ✅ 精确 |
| Permission 检查模式 | ✅ 可扫描 | ⚠️ 可扫描但变量名不同 |
| Binder 接口实现 | ✅ 精确 | ✅ 精确 |

> [!NOTE]
> 对于图谱的核心需求（类/方法/继承/Binder/Service 注册），反编译精度完全足够。精度损失主要在方法体内部逻辑，但图谱关注的是**结构关系**而非**实现细节**。

---

## 三、建议的实施优先级

```text
第 1 步（1 天）：Universal Ctags Kotlin 集成
  → parser_registry.toml 启用 Kotlin
  → ctags_importer.py 添加 Kotlin KIND_MAP
  → PACKAGE_RE 去掉分号要求
  → 测试通过

第 2 步（2-3 天）：userdebug 产物提取脚本
  → extract_vendor.sh（adb pull 自动化）
  → decompile_vendor.sh（jadx 批量反编译）
  → TOML 配置模板
  → diff 查询 SQL

第 3 步（1 天）：runtime collector 原型
  → 解析 service list / dumpsys package
  → 与静态图谱做 Service 差集

第 4 步（按需）：Tree-sitter-kotlin 精确 AST
  → 补充 sealed class、companion object 等语义
  → 扩展函数的 receiver type 关系
```

> [!IMPORTANT]
> 第 1 步和第 2 步可以**并行**。Ctags Kotlin 解决 AOSP 侧的 Kotlin 源码，jadx 解决厂商侧的产物。两者产出的都是 Ctags JSONL → 同一个 SQLite 图谱。
