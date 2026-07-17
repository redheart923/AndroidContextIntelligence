from pathlib import Path
from graph.writer import Node, Edge, stable_id
from collectors.permission.xml_permission_importer import extract_permissions

def test_extract_permissions_from_manifest(tmp_path):
    source_root = tmp_path
    xml_path = source_root / "AndroidManifest.xml"
    
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.app">
    
    <permission android:name="android.permission.XYZ" />
    <permission android:name="com.example.app.CUSTOM_PERM" android:protectionLevel="signature" />
    
</manifest>
"""
    xml_path.write_text(xml_content, encoding="utf-8")
    
    results = extract_permissions(xml_path, source_root)
    
    assert len(results) == 2
    
    node1, edge1 = results[0]
    assert isinstance(node1, Node)
    assert node1.node_type == "PERMISSION"
    assert node1.display_name == "android.permission.XYZ"
    assert node1.node_id == stable_id("PERMISSION", "android.permission.XYZ")
    assert node1.extractor == "xml_permission_importer"
    
    assert isinstance(edge1, Edge)
    assert edge1.edge_type == "DECLARED_IN"
    assert edge1.from_node_id == node1.node_id
    assert edge1.to_node_id == stable_id("FILE", "AndroidManifest.xml")
    assert edge1.extractor == "xml_permission_importer"
    
    node2, edge2 = results[1]
    assert node2.display_name == "com.example.app.CUSTOM_PERM"
