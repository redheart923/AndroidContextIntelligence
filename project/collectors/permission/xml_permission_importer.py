import xml.etree.ElementTree as ET
from pathlib import Path
from graph.writer import Node, Edge, stable_id

EXTRACTOR_NAME = "xml_permission_importer"

def extract_permissions(xml_path: Path, source_root: Path) -> list[tuple[Node, Edge]]:
    results = []

    try:
        # We need to handle the android namespace properly
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Namespace map
        ns = {'android': 'http://schemas.android.com/apk/res/android'}

        rel_path = str(xml_path.relative_to(source_root)).replace("\\", "/")
        file_id = stable_id("FILE", rel_path)

        for perm in root.findall('.//permission'):
            perm_name = perm.get(f"{{{ns['android']}}}name")
            if not perm_name:
                continue

            node_id = stable_id("PERMISSION", perm_name)
            node = Node(
                node_id=node_id,
                node_type="PERMISSION",
                display_name=perm_name,
                qualified_name=perm_name,
                source_path=rel_path,
                extractor=EXTRACTOR_NAME
            )

            edge = Edge(
                edge_type="DECLARED_IN",
                from_node_id=node_id,
                to_node_id=file_id,
                source_path=rel_path,
                extractor=EXTRACTOR_NAME
            )

            results.append((node, edge))

    except Exception as e:
        # In a real implementation we might log this
        pass

    return results
