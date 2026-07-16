from __future__ import annotations
import argparse,json,sqlite3
from collections import defaultdict
from pathlib import Path
from graph.writer import GraphWriter
from collectors.service.service_registration_importer import (scan_sources,ConstantResolver,DbTypeIndex,find_registration_calls,build_fact,import_fact)
from workspace.pipeline import load_plan,repositories_for,scan_paths,source_allowed


def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--plan",type=Path,required=True);p.add_argument("--db",type=Path,required=True);p.add_argument("--report",type=Path,required=True);a=p.parse_args()
    plan=load_plan(a.plan);root=Path(plan["aosp_root"]);sources=[];source_repo={}
    defaults=plan.get("default_exclude",[])
    for repo in repositories_for(plan,"java","service_registration"):
        for path in scan_paths(root,repo):
            items=[x for x in scan_sources(path,root) if source_allowed(root,repo,x.path,defaults)];sources.extend(items)
            for item in items: source_repo[item.source_path]=repo["name"]
    constants=ConstantResolver(sources)
    with sqlite3.connect(a.db) as connection: types=DbTypeIndex(connection)
    facts=[build_fact(source,call,constants,types) for source in sources for call in find_registration_calls(source)]
    writer=GraphWriter(a.db)
    try:
        for fact in facts: import_fact(writer,fact,types)
    finally: writer.close()
    summary=defaultdict(int)
    for fact in facts: summary[fact.api]+=1;summary[f"status:{fact.resolution_status}"]+=1
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps({"summary":dict(sorted(summary.items())),"registrations":[{"registration_id":f.registration_id,"api":f.api,"resolved_key":f.resolved_key,"resolved_instance_type":f.resolved_instance_type,"resolution_status":f.resolution_status,"source_path":f.source_path,"repository":source_repo.get(f.source_path),"line":f.line} for f in facts]},indent=2),encoding="utf-8")
    print(f"Service registrations: {len(facts)}; resolved: {sum(f.resolution_status=='resolved' for f in facts)}")
    return 0
if __name__=="__main__": raise SystemExit(main())
