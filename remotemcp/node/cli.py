from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from remotemcp.durable.errors import DurableError
from .client import NodeClient
from .config import NodeConfig
from .db import NodeDatabase
from .identity import NodeIdentity
from .service import NodeService


def _read_delete_code(path)->str:
    p=path if hasattr(path,"read_text") else Path(path).resolve()
    text=p.read_text(encoding="utf-8").strip()
    if not text or "\n" in text or "\r" in text:
        raise DurableError("DEVICE_PAIRING_INVALID","pairing code file must contain exactly one non-empty line")
    try:
        p.unlink()
    except OSError as exc:
        raise DurableError("PAIRING_CODE_FILE_DELETE_FAILED","could not delete pairing code file before network use") from exc
    return text


def parser():
    p=argparse.ArgumentParser(prog="python -m remotemcp.node")
    sub=p.add_subparsers(dest="command",required=True)
    pair=sub.add_parser("pair")
    pair.add_argument("--url",required=True)
    pair.add_argument("--pairing-id",required=False)
    pair.add_argument("--code-file",required=True)
    pair.add_argument("--name",required=True)
    pair.add_argument("--root",required=True)
    pair.add_argument("--runtime-dir",required=True)
    run=sub.add_parser("run")
    run.add_argument("--url",required=False)
    run.add_argument("--root",required=False)
    run.add_argument("--legacy-root",action="append",default=[])
    run.add_argument("--runtime-dir",required=True)
    status=sub.add_parser("status")
    status.add_argument("--runtime-dir",required=True)
    doctor=sub.add_parser("doctor")
    doctor.add_argument("--url",required=False)
    doctor.add_argument("--root",required=False)
    doctor.add_argument("--legacy-root",action="append",default=[])
    doctor.add_argument("--runtime-dir",required=True)
    audit=sub.add_parser("process-audit",help="Read-only reverse PID to durable node job mapping")
    audit.add_argument("--runtime-dir",required=True)
    audit.add_argument("--pid",action="append",type=int,default=[])
    return p


async def do_pair(a):
    raw=_read_delete_code(Path(a.code_file).resolve())
    pairing_id=a.pairing_id
    code=raw
    if not pairing_id:
        if "|" not in raw:
            raise DurableError("DEVICE_PAIRING_INVALID","--pairing-id required unless code file contains pairing_id|pairing_code")
        pairing_id,code=raw.split("|",1)
    elif "|" in raw:
        embedded,embedded_code=raw.split("|",1)
        if embedded!=pairing_id:
            raise DurableError("DEVICE_PAIRING_INVALID","pairing id in code file does not match --pairing-id")
        code=embedded_code
    cfg=NodeConfig.create(a.url,Path(a.root),Path(a.runtime_dir))
    db=NodeDatabase(cfg.runtime_dir);db.bootstrap()
    ident=NodeIdentity(cfg.runtime_dir,db)
    if ident.paired:
        raise DurableError("NODE_IDENTITY_MISMATCH","node is already paired")
    client=NodeClient(cfg,ident)
    try:
        resp=await client.pair(pairing_id,code,a.name)
    finally:
        await client.close()
    ident.persist_paired(resp,origin=cfg.origin,root=cfg.root,device_name=a.name)
    print(json.dumps({
        "device_id":resp["device_id"],"state":resp["state"],
        "key_fingerprint_sha256":resp["key_fingerprint_sha256"],
    },indent=2))


def _stored_config(runtime_dir:Path,url=None,root=None,legacy_roots=()):
    db=NodeDatabase(runtime_dir);db.bootstrap()
    ident=NodeIdentity(runtime_dir,db)
    if not ident.paired:
        raise DurableError("NODE_IDENTITY_MISMATCH","node is not paired")
    return NodeConfig.create(
        url or ident.device["origin"],
        Path(root or ident.device["root"]),
        runtime_dir,
        legacy_roots=legacy_roots,
    ),db,ident


async def do_run(a):
    cfg,_,_= _stored_config(Path(a.runtime_dir),a.url,a.root,a.legacy_root)
    service=NodeService(cfg)
    await service.run_forever()


def do_status(a):
    _,db,ident=_stored_config(Path(a.runtime_dir))
    print(json.dumps({"paired":True,"device":ident.device,"schema":db.get_meta("schema")},indent=2))


def do_doctor(a):
    cfg,db,ident=_stored_config(Path(a.runtime_dir),a.url,a.root,a.legacy_root)
    checks={
        "paired":ident.paired,"root_exists":cfg.root.is_dir(),
        "runtime_exists":cfg.runtime_dir.is_dir(),"origin":cfg.origin,
        "device_id":ident.device["device_id"],"schema":db.get_meta("schema"),
        "legacy_roots":[str(p) for p in cfg.legacy_roots],
    }
    print(json.dumps(checks,indent=2))


def main(argv=None):
    a=parser().parse_args(argv)
    if a.command=="pair":return asyncio.run(do_pair(a))
    if a.command=="run":return asyncio.run(do_run(a))
    if a.command=="status":return do_status(a)
    if a.command=="doctor":return do_doctor(a)
    if a.command=="process-audit":
        from .process_ownership_audit import audit_process_ownership
        # Unlike _stored_config, this NEVER bootstraps or mutates SQLite.
        report=audit_process_ownership(Path(a.runtime_dir),a.pid)
        print(json.dumps(report,indent=2,ensure_ascii=False))
        return 0
    raise SystemExit(2)
