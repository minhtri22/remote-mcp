from __future__ import annotations
import hashlib,json,os,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from remotemcp.durable.models import now_ms
from remotemcp.node.db import NodeDatabase
from remotemcp.node.command_journal import NodeCommandJournal
from remotemcp.node.projects import NodeProjects
from remotemcp.node.worktrees import NodeWorktrees
from remotemcp.node.cas import NodeCas

def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2bd-cas-") as td:
        base=Path(td);root=base/"root";proj=root/"p";proj.mkdir(parents=True);rt=base/"rt"
        db=NodeDatabase(rt);db.bootstrap();projects=NodeProjects(db,root);projects.bind("prj",1,"p","NON_GIT")
        with db.transaction() as con:
            t=now_ms();con.execute("insert into node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) values('tsk','prj',1,NULL,NULL,'ACTIVE',?,?)",(t,t))
        journal=NodeCommandJournal(db);wt=NodeWorktrees(db,root,projects)
        target=proj/"x.txt";target.write_bytes(b"old")
        before=hashlib.sha256(b"old").hexdigest();after=hashlib.sha256(b"new").hexdigest()
        env={"command_id":"cmd","route_generation":1,"operation_id":"op","project_id":"prj","task_id":"tsk","command_type":"FILE_WRITE_CAS","request_hash":"rh","payload":{"task_id":"tsk"},"issued_at_ms":now_ms(),"command_expires_at_ms":now_ms()+60000}
        journal.receive(env)
        temp=proj/".x.txt.cmd.cas.tmp";temp.write_bytes(b"new")
        with db.transaction() as con:
            con.execute("insert into node_cas_mutations(command_id,task_id,path_rel,expected_before_hash,intended_after_hash,temp_rel,state,created_at_ms,updated_at_ms) values('cmd','tsk','x.txt',?,?,?,'PREPARED',?,?)",(before,after,temp.name,now_ms(),now_ms()))
        os.replace(temp,target)
        # Recreate every node-side repository after the crash boundary.
        db2=NodeDatabase(rt);db2.bootstrap();p2=NodeProjects(db2,root);w2=NodeWorktrees(db2,root,p2);j2=NodeCommandJournal(db2);cas2=NodeCas(db2,w2,j2)
        cas2.reconcile_all()
        assert target.read_bytes()==b"new"
        assert db2.query_one("select state from node_cas_mutations where command_id='cmd'")["state"]=="COMMITTED"
        assert j2.get("cmd")["state"]=="SUCCEEDED"
        print(json.dumps({"verdict":"PASS","after_hash":after,"second_replace":False,"command_state":"SUCCEEDED"},indent=2))
if __name__=="__main__":main()