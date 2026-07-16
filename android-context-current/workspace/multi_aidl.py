from __future__ import annotations
import argparse, json
from pathlib import Path
from graph.writer import GraphWriter
from collectors.binder.aidl_binder_importer import (scan_aidl_files, build_simple_name_index,
    scan_java_binder_relations, import_aidl_interfaces, import_binder_relations)
from workspace.pipeline import (load_plan, repositories_for, scan_paths,
    repository_for_source, source_allowed)
from graph.writer import stable_id
import sqlite3


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--plan",type=Path,required=True);p.add_argument("--db",type=Path,required=True);p.add_argument("--report",type=Path,required=True);a=p.parse_args()
    plan=load_plan(a.plan); root=Path(plan["aosp_root"]); interfaces=[]; failures=[]
    repos=repositories_for(plan,"aidl","symbols")
    defaults=plan.get("default_exclude",[])
    for repo in repos:
        for source in scan_paths(root,repo):
            found, errors=scan_aidl_files(source)
            interfaces.extend(x for x in found if source_allowed(root,repo,x.source_path,defaults))
            failures.extend((x,e) for x,e in errors if source_allowed(root,repo,x,defaults))
    index=build_simple_name_index(interfaces); relations=[]
    for repo in repositories_for(plan,"java","symbols"):
        for source in scan_paths(root,repo):
            relations.extend(x for x in scan_java_binder_relations(source,index)
                if source_allowed(root,repo,x.source_path,defaults))
    writer=GraphWriter(a.db)
    try: ic,mc=import_aidl_interfaces(writer,interfaces,root)
    finally: writer.close()
    writer=GraphWriter(a.db)
    try: rc,uc=import_binder_relations(writer,a.db,relations,root)
    finally: writer.close()
    a.report.parent.mkdir(parents=True,exist_ok=True)
    unresolved=[]
    with sqlite3.connect(a.db) as connection:
        for relation in relations:
            if (not connection.execute("SELECT 1 FROM node WHERE node_id=?",(stable_id("JAVA_CLASS",relation.implementation_qname),)).fetchone()
                or not connection.execute("SELECT 1 FROM node WHERE node_id=?",(stable_id("AIDL_INTERFACE",relation.aidl_qname),)).fetchone()):
                repo=repository_for_source(root,plan["repositories"],relation.source_path)
                unresolved.append({"implementation":relation.implementation_qname,"aidl_interface":relation.aidl_qname,
                    "source_path":str(relation.source_path),"repository":repo["name"] if repo else None})
    a.report.write_text(json.dumps({"summary":{"interfaces":ic,"methods":mc,"binder_relations":rc,"unresolved":uc,"failures":len(failures)},
        "repositories":[x["name"] for x in repos],"unresolved_binder_relations":unresolved,
        "failures":[{"path":str(x),"error":str(e),"repository":(repository_for_source(root,repos,x) or {}).get("name")} for x,e in failures]},indent=2),encoding="utf-8")
    print(f"AIDL interfaces: {ic}; methods: {mc}; Binder relations: {rc}; unresolved: {uc}")
    return 0
if __name__=="__main__": raise SystemExit(main())
