import pytest
from pathlib import Path
from graph.writer import Edge, stable_id
from collectors.permission.java_permission_scanner import scan_file_for_permissions

def test_scan_permissions(tmp_path):
    java_file = tmp_path / "Dummy.java"
    java_content = """package com.example;
@android.annotation.RequiresPermission("android.permission.XYZ")
public void doSomething() {
    // some code
    checkPermission("android.permission.ABC", pid, uid);
}
"""
    java_file.write_text(java_content, encoding="utf-8")
    
    # Dummy methods list: [(line_start, "method_node_id")]
    # doSomething is at line 3.
    methods = [(3, "method_node_id")]
    
    edges = scan_file_for_permissions(java_file, tmp_path, methods)
    
    assert len(edges) == 2
    
    # First edge: REQUIRES_PERMISSION
    assert edges[0].edge_type == "REQUIRES_PERMISSION"
    assert edges[0].from_node_id == "method_node_id"
    assert edges[0].to_node_id == stable_id("PERMISSION", "android.permission.XYZ")
    assert edges[0].line_start == 2
    
    # Second edge: ENFORCES_PERMISSION
    assert edges[1].edge_type == "ENFORCES_PERMISSION"
    assert edges[1].from_node_id == "method_node_id"
    assert edges[1].to_node_id == stable_id("PERMISSION", "android.permission.ABC")
    assert edges[1].line_start == 5

def test_scan_permissions_anyof(tmp_path):
    java_file = tmp_path / "DummyAnyOf.java"
    java_content = """package com.example;
@RequiresPermission(anyOf={"android.permission.FOO", "android.permission.BAR"})
public void doSomething() {
}
"""
    java_file.write_text(java_content, encoding="utf-8")
    methods = [(3, "method_node_id")]
    edges = scan_file_for_permissions(java_file, tmp_path, methods)
    
    assert len(edges) == 2
    assert edges[0].edge_type == "REQUIRES_PERMISSION"
    assert edges[0].to_node_id == stable_id("PERMISSION", "android.permission.FOO")
    assert edges[1].edge_type == "REQUIRES_PERMISSION"
    assert edges[1].to_node_id == stable_id("PERMISSION", "android.permission.BAR")

def test_scan_permissions_enforce(tmp_path):
    java_file = tmp_path / "DummyEnforce.java"
    java_content = """package com.example;
public void doSomething() {
    mContext.enforceCallingOrSelfPermission("android.permission.BAZ", "message");
}
"""
    java_file.write_text(java_content, encoding="utf-8")
    methods = [(2, "method_node_id")]
    edges = scan_file_for_permissions(java_file, tmp_path, methods)
    
    assert len(edges) == 1
    assert edges[0].edge_type == "ENFORCES_PERMISSION"
    assert edges[0].to_node_id == stable_id("PERMISSION", "android.permission.BAZ")
    assert edges[0].line_start == 3
