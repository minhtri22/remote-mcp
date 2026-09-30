from __future__ import annotations

import hashlib,json,os
import pytest

from remotemcp.node.db import NodeDatabase
from remotemcp.node.command_journal import NodeCommandJournal
from remotemcp.node.projects import NodeProjects
from remotemcp.node.worktrees import NodeWorktrees
from remotemcp.node.cas import NodeCas
from remotemcp.durable.models import now_ms


def setup_non_git(tmp_path):
    root=tmp_path/"root";proj=root/"p";proj.mkdir(parents=True)
    rt=tmp_path/"rt";db=NodeDatabase(rt);db.bootstrap()
    projects=NodeProjects(db,root);projects.bind("prj",1,"p","NON_GIT")
    with db.transaction() as con:
        t=now_ms()
        con.execute("insert into node_tasks(task_id,project_id,binding_generation,worktree_rel,branch_name,state,created_at_ms,updated_at_ms) values('tsk','prj',1,NULL,NULL,'ACTIVE',?,?)",(t,t))
    journal=NodeCommandJournal(db);wt=NodeWorktrees(db,root,projects);cas=NodeCas(db,wt,journal)
    return root,proj,db,journal,cas


def receive(journal,command_id,expires):
    payload={"task_id":"tsk","path":"x.txt","expected_sha256":"x","content":"new"}
    envelope={"command_id":command_id,"route_generation":1,"operation_id":"op-"+command_id,"project_id":"prj","task_id":"tsk","command_type":"FILE_WRITE_CAS","request_hash":"rh-"+command_id,"payload":payload,"issued_at_ms":now_ms(),"command_expires_at_ms":expires}
    journal.receive(envelope)


def test_node_cas_reconciles_crash_after_replace_without_second_write(tmp_path):
    root,proj,db,journal,cas=setup_non_git(tmp_path)
    target=proj/"x.txt";target.write_bytes(b"old")
    before=hashlib.sha256(b"old").hexdigest();after=hashlib.sha256(b"new").hexdigest()
    receive(journal,"cmd",now_ms()+60000)
    temp=proj/".x.txt.cmd.cas.tmp";temp.write_bytes(b"new")
    with db.transaction() as con:
        con.execute("insert into node_cas_mutations(command_id,task_id,path_rel,expected_before_hash,intended_after_hash,temp_rel,state,created_at_ms,updated_at_ms) values('cmd','tsk','x.txt',?,?,?,'PREPARED',?,?)",(before,after,temp.name,now_ms(),now_ms()))
    os.replace(temp,target)
    cas.reconcile_all()
    assert target.read_bytes()==b"new"
    assert db.query_one("select state from node_cas_mutations where command_id='cmd'")["state"]=="COMMITTED"
    assert journal.get("cmd")["state"]=="SUCCEEDED"


def test_expired_prepared_cas_does_not_replace(tmp_path):
    root,proj,db,journal,cas=setup_non_git(tmp_path)
    target=proj/"x.txt";target.write_bytes(b"old")
    before=hashlib.sha256(b"old").hexdigest();after=hashlib.sha256(b"new").hexdigest()
    receive(journal,"cmd2",now_ms()-1)
    temp=proj/".x.txt.cmd2.cas.tmp";temp.write_bytes(b"new")
    with db.transaction() as con:
        con.execute("insert into node_cas_mutations(command_id,task_id,path_rel,expected_before_hash,intended_after_hash,temp_rel,state,created_at_ms,updated_at_ms) values('cmd2','tsk','x.txt',?,?,?,'PREPARED',?,?)",(before,after,temp.name,now_ms(),now_ms()))
    cas.reconcile_all()
    assert target.read_bytes()==b"old"
    assert journal.get("cmd2")["state"]=="IN_DOUBT"
