import re
from pathlib import Path
from typing import Any
from graph.writer import Node, Edge, stable_id

REQUIRES_PERMISSION_RE = re.compile(r'@RequiresPermission\s*\((.*?)\)')
STRING_LITERAL_RE = re.compile(r'"([a-zA-Z0-9_.]+)"')
CHECK_PERMISSION_RE = re.compile(r'\b(?:checkPermission|enforceCallingOrSelfPermission|enforceCallingPermission|enforcePermission)\s*\(\s*"([a-zA-Z0-9_.]+)"')

def load_methods(db_connection: Any, source_path: str) -> list[tuple[int, str]]:
    """
    Returns [(line_start, method_node_id)] ordered by line_start.
    """
    cursor = db_connection.cursor()
    cursor.execute('''
        SELECT line_start, node_id
        FROM node
        WHERE source_path = ? AND node_type IN ('JAVA_METHOD', 'KOTLIN_METHOD')
        ORDER BY line_start ASC
    ''', (source_path,))
    return [(row[0], row[1]) for row in cursor.fetchall() if row[0] is not None]

def scan_file_for_permissions(file_path: Path, source_root: Path, methods: list[tuple[int, str]]) -> list[tuple[Node, Edge]]:
    """
    Scans a Java/Kotlin file line by line to extract permission requirements and enforcements.
    """
    edges = []

    try:
        # Use relative path if possible, fallback to absolute
        relative_path = str(file_path.relative_to(source_root)).replace('\\', '/')
    except ValueError:
        relative_path = str(file_path).replace('\\', '/')

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f):
            line_num = i + 1

            # 1. Look for @RequiresPermission
            req_match = REQUIRES_PERMISSION_RE.search(line)
            if req_match:
                inner_args = req_match.group(1)
                perms = STRING_LITERAL_RE.findall(inner_args)
                for perm in perms:
                    # For annotations, find the first method where line_start >= line_num
                    method_id = None
                    for m_start, m_id in methods:
                        if m_start >= line_num:
                            method_id = m_id
                            break

                    if method_id:
                        perm_node = Node(
                            node_id=stable_id("PERMISSION", perm),
                            node_type="PERMISSION",
                            display_name=perm,
                            qualified_name=perm,
                            extractor="java_permission_scanner"
                        )
                        edges.append((
                            perm_node,
                            Edge(
                                edge_type="REQUIRES_PERMISSION",
                                from_node_id=method_id,
                                to_node_id=perm_node.node_id,
                                source_path=relative_path,
                                line_start=line_num,
                                extractor="java_permission_scanner"
                            )
                        ))

            # 2. Look for checkPermission / enforceCallingOrSelfPermission
            for check_match in CHECK_PERMISSION_RE.finditer(line):
                perm = check_match.group(1)

                # For body calls, find the last method where line_start <= line_num
                method_id = None
                for m_start, m_id in reversed(methods):
                    if m_start <= line_num:
                        method_id = m_id
                        break

                if method_id:
                    perm_node = Node(
                        node_id=stable_id("PERMISSION", perm),
                        node_type="PERMISSION",
                        display_name=perm,
                        qualified_name=perm,
                        extractor="java_permission_scanner"
                    )
                    edges.append((
                        perm_node,
                        Edge(
                            edge_type="ENFORCES_PERMISSION",
                            from_node_id=method_id,
                            to_node_id=perm_node.node_id,
                            source_path=relative_path,
                            line_start=line_num,
                            extractor="java_permission_scanner"
                        )
                    ))

    return edges
