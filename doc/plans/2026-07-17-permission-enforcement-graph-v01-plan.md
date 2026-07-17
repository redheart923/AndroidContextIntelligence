# Permission Enforcement Graph v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 2a (Permission Enforcement Graph) by extracting permission declarations from XML and permission checks/annotations from Java/Kotlin source code, integrating them into the existing multi-repository workspace.

**Architecture:** We will introduce a new `collectors/permission` package. First, an XML parser extracts `<permission>` definitions from `AndroidManifest.xml`, `privapp-permissions*.xml`, etc., yielding `PERMISSION` nodes. Second, a heuristic scanner reads Java/Kotlin files line-by-line, matching permission enforcement signatures (`checkPermission`, `@RequiresPermission`, etc.). It cross-references these matches against `JAVA_METHOD` and `KOTLIN_METHOD` line boundaries already extracted by Ctags in the DB, establishing `ENFORCES_PERMISSION` and `REQUIRES_PERMISSION` edges. We then add a `multi_permission.py` pipeline step and generate an installer.

**Tech Stack:** Python 3.11+, SQLite, Regex, standard library `xml.etree.ElementTree`.

## Global Constraints

- Default AOSP root: `/home/ts/aosp`.
- Default project root: `/home/ts/android-context-intelligence`.
- Use the existing GraphWriter (`graph/writer.py`).
- Do not introduce heavy dependencies like CodeQL in this version.
- Ensure the pipeline can tolerate missing owners (e.g. methods not found in DB).
- Use `installers/install_permission_enforcement_graph_v01.sh` as the final delivery payload.

---

### Task 1: Extend Workspace for XML Support

**Files:**
- Modify: `android-context-current/workspace/languages.py`
- Modify: `android-context-current/config/parser_registry.toml`
- Test: `android-context-current/tests/unit/test_workspace_languages.py` (if exists, else create minimal test)

**Interfaces:**
- `detect_languages` should recognize `.xml` as `xml`.
- `parser_registry.toml` should define a `parsers.xml` with capability `permission_declaration`.

- [ ] **Step 1: Modify languages.py to support XML**

```python
# Add ".xml": "xml" to SUFFIXES dict
```

- [ ] **Step 2: Modify parser_registry.toml**

```toml
# Add:
[parsers.xml]
implementation = "xml_permission_importer"
enabled = true
capabilities = ["permission_declaration"]
```

- [ ] **Step 3: Modify parser_registry.toml to add capability to Java/Kotlin**

```toml
# In [parsers.java] add "permission_enforcement" to capabilities if not already there.
# In [parsers.kotlin] add "permission_enforcement" to capabilities. (Enable kotlin parser entry to use java_permission_scanner)
```

- [ ] **Step 4: Commit**

```bash
git add workspace/languages.py config/parser_registry.toml
git commit -m "feat: add XML language detection and permission capabilities"
```

### Task 2: XML Permission Importer

**Files:**
- Create: `android-context-current/collectors/permission/xml_permission_importer.py`
- Create: `android-context-current/tests/unit/test_xml_permission_importer.py`

**Interfaces:**
- `extract_permissions(xml_path: Path, source_root: Path) -> list[tuple[Node, Edge]]`

- [ ] **Step 1: Write the failing test**

```python
def test_extract_permissions_from_manifest(tmp_path):
    # Setup dummy AndroidManifest.xml with <permission android:name="android.permission.XYZ" />
    # call extract_permissions
    # Assert nodes and edges returned
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Write minimal implementation**

```python
import xml.etree.ElementTree as ET
# Use ET to parse XML, look for ".//permission", extract android:name
# Build Node(node_type="PERMISSION", ...)
# Build Edge(edge_type="DECLARED_IN", from_node_id=node.node_id, to_node_id=file_id, ...)
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

### Task 3: Java/Kotlin Permission Scanner

**Files:**
- Create: `android-context-current/collectors/permission/java_permission_scanner.py`
- Create: `android-context-current/tests/unit/test_java_permission_scanner.py`

**Interfaces:**
- `load_methods(db_connection, source_path: str) -> list[tuple[int, str]]` (returns `[(line_start, method_node_id)]` ordered by line)
- `scan_file_for_permissions(file_path: Path, source_root: Path, methods: list) -> list[Edge]`

- [ ] **Step 1: Write the failing test**

```python
def test_scan_permissions(tmp_path):
    # Setup dummy java file with @RequiresPermission("foo") on line 2, and checkPermission("bar") on line 5
    # Dummy methods list: [(3, "method_node_id")]
    # Assert returns edges ENFORCES_PERMISSION and REQUIRES_PERMISSION
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Write minimal implementation**

```python
# Regex for @RequiresPermission\([^)]*\"([a-zA-Z0-9_.]+)\"
# Regex for checkPermission\(\s*\"([a-zA-Z0-9_.]+)\"
# For each line matched, find the associated method:
# For annotations (RequiresPermission), find the first method where line_start >= line
# For checks (checkPermission), find the last method where line_start <= line
# Produce Edge(edge_type="REQUIRES_PERMISSION" | "ENFORCES_PERMISSION", to_node_id=stable_id("PERMISSION", perm))
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

### Task 4: Workspace Integration (multi_permission.py)

**Files:**
- Create: `android-context-current/workspace/multi_permission.py`
- Modify: `android-context-current/scripts/rebuild_all.sh`

**Interfaces:**
- Command line tool taking `--plan`, `--db`, `--report`

- [ ] **Step 1: Write multi_permission.py**

```python
# Iterates through repos in plan
# For XML files: calls xml_permission_importer
# For Java/Kt files: calls java_permission_scanner
# Writes nodes and edges to GraphWriter
# Writes report JSON
```

- [ ] **Step 2: Update rebuild_all.sh**

```bash
# Add python -m workspace.multi_permission after annotation pipeline
```

- [ ] **Step 3: Test integration**

```bash
cd android-context-current && ./scripts/rebuild_all.sh --plan-only
```

- [ ] **Step 4: Commit**

### Task 5: Installer Packaging

**Files:**
- Create: `installers/install_permission_enforcement_graph_v01.sh`
- Modify: `setup.sh` (to call it)
- Modify: `README.md` (to list it)

- [ ] **Step 1: Create the installer script**

```bash
# Copy install_multi_repository_source_configuration_v01.sh as base
# Replace payload extraction with the new files:
# workspace/languages.py, config/parser_registry.toml, workspace/multi_permission.py
# collectors/permission/*, scripts/rebuild_all.sh
```

- [ ] **Step 2: Add to setup.sh and README.md**

- [ ] **Step 3: Test installation on a fresh directory**

- [ ] **Step 4: Commit**
