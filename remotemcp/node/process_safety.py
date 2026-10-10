from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from remotemcp.durable.errors import DurableError


_TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}


def _now_ms()->int:
    return time.time_ns()//1_000_000


def _canonical_sha256(value)->str:
    raw=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ProcessSafetyProbe:
    """Physical process safety snapshot for maintenance/restart admission.

    The probe is intentionally fail-closed. It joins current OS process state
    with durable RemoteMCP worker provenance and separately evaluates explicit
    long-lived process declarations.

    It never returns full process command lines to the gateway.
    """

    def __init__(self,jobs,*,max_processes:int=8192,max_job_roots:int=4096):
        self.jobs=jobs
        self.max_processes=max(128,int(max_processes))
        self.max_job_roots=max(128,int(max_job_roots))

    @staticmethod
    def _parse_declarations()->list[str]:
        raw=os.environ.get("REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS","").strip()
        if not raw:
            return []
        values=[]
        if raw.startswith("["):
            try:
                parsed=json.loads(raw)
                if isinstance(parsed,list):
                    values=[str(x).strip() for x in parsed]
            except Exception:
                values=[]
        if not values:
            values=[x.strip() for x in raw.split(";")]
        out=[]
        seen=set()
        for value in values:
            if not value:
                continue
            key=value.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(value)
            if len(out)>=64:
                break
        return out

    @staticmethod
    def _windows_processes()->list[dict]:
        script=(
            "$ErrorActionPreference='Stop';"
            "$epoch=[datetime]'1970-01-01T00:00:00Z';"
            "$rows=Get-CimInstance Win32_Process | ForEach-Object {"
            "$created=$null;"
            "if($_.CreationDate){$created=[int64](($_.CreationDate.ToUniversalTime()-$epoch).TotalMilliseconds)};"
            "[pscustomobject]@{"
            "pid=[int]$_.ProcessId;"
            "ppid=[int]$_.ParentProcessId;"
            "name=[string]$_.Name;"
            "command_line=[string]$_.CommandLine;"
            "created_at_ms=$created"
            "}"
            "};"
            "$rows|ConvertTo-Json -Compress -Depth 3"
        )
        proc=subprocess.run(
            ["powershell.exe","-NoProfile","-Command",script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            # Suppress Windows Terminal/PowerShell popup on headless node probes.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode!=0:
            raise RuntimeError("windows process enumeration failed")
        raw=(proc.stdout or "").strip()
        if not raw:
            return []
        payload=json.loads(raw)
        if isinstance(payload,dict):
            payload=[payload]
        if not isinstance(payload,list):
            raise RuntimeError("windows process enumeration returned invalid payload")
        out=[]
        for row in payload:
            if not isinstance(row,dict):
                continue
            try:
                pid=int(row.get("pid"))
                ppid=int(row.get("ppid") or 0)
            except Exception:
                continue
            if pid<=0:
                continue
            created=row.get("created_at_ms")
            try:
                created=int(created) if created is not None else None
            except Exception:
                created=None
            out.append({
                "pid":pid,
                "ppid":ppid,
                "name":str(row.get("name") or ""),
                "command_line":str(row.get("command_line") or ""),
                "created_at_ms":created,
            })
        return out

    @staticmethod
    def _linux_processes()->list[dict]:
        proc_root=Path("/proc")
        if not proc_root.is_dir():
            raise RuntimeError("/proc is unavailable")
        btime=None
        for line in (proc_root/"stat").read_text(encoding="utf-8",errors="replace").splitlines():
            if line.startswith("btime "):
                btime=int(line.split()[1])
                break
        if btime is None:
            raise RuntimeError("/proc/stat btime is unavailable")
        ticks=int(os.sysconf("SC_CLK_TCK"))
        out=[]
        for child in proc_root.iterdir():
            if not child.name.isdigit():
                continue
            try:
                stat=(child/"stat").read_text(encoding="utf-8",errors="replace")
                rparen=stat.rfind(")")
                fields=stat[rparen+2:].split()
                ppid=int(fields[1])
                start_ticks=int(fields[19])
                created_at_ms=int((btime+(start_ticks/ticks))*1000)
                name=(child/"comm").read_text(encoding="utf-8",errors="replace").strip()
                raw=(child/"cmdline").read_bytes()
                command_line=raw.replace(b"\x00",b" ").decode("utf-8",errors="replace").strip()
                out.append({
                    "pid":int(child.name),
                    "ppid":ppid,
                    "name":name,
                    "command_line":command_line,
                    "created_at_ms":created_at_ms,
                })
            except (FileNotFoundError,ProcessLookupError,PermissionError,ValueError,IndexError):
                continue
        return out

    def _enumerate_processes(self)->list[dict]:
        if os.name=="nt":
            return self._windows_processes()
        if sys.platform.startswith("linux"):
            return self._linux_processes()
        raise RuntimeError(f"unsupported process safety platform: {sys.platform}")

    def _job_roots(self)->tuple[list[dict],int]:
        rows=self.jobs.db.query_all(
            "SELECT * FROM node_routed_jobs ORDER BY created_at_ms DESC"
        )
        if len(rows)>self.max_job_roots:
            return [],len(rows)-self.max_job_roots
        roots=[]
        unresolved=0
        for row in rows:
            node_job_id=row["node_job_id"]
            if not node_job_id:
                continue
            try:
                durable_row=self.jobs.durable.jobs.get(node_job_id)
                state=self.jobs.durable.job_get(node_job_id)
                fp=json.loads(durable_row["worker_fingerprint_json"] or "{}")
                if not isinstance(fp,dict):
                    fp={}
                pid=fp.get("pid")
                if pid is None:
                    continue
                pid=int(pid)
                if pid<=0:
                    continue
                started=state.get("started_at_ms")
                try:
                    started=int(started) if started is not None else None
                except Exception:
                    started=None
                terminal_at=row["terminal_at_ms"]
                try:
                    terminal_at=int(terminal_at) if terminal_at is not None else None
                except Exception:
                    terminal_at=None
                roots.append({
                    "proxy_job_id":row["proxy_job_id"],
                    "node_job_id":node_job_id,
                    "pid":pid,
                    "state":str(state.get("state") or row["state"]),
                    "started_at_ms":started,
                    "terminal_at_ms":terminal_at,
                })
            except (DurableError,KeyError,TypeError,ValueError,json.JSONDecodeError):
                unresolved+=1
        return roots,unresolved

    @staticmethod
    def _within_job_window(proc:dict,root:dict)->bool:
        created=proc.get("created_at_ms")
        started=root.get("started_at_ms")
        terminal=root.get("terminal_at_ms")
        if created is None or started is None:
            return True
        if created < started-5_000:
            return False
        if terminal is not None and created > terminal+5_000:
            return False
        return True

    def snapshot(self)->dict:
        observed=_now_ms()
        try:
            processes=self._enumerate_processes()
            if len(processes)>self.max_processes:
                raise RuntimeError("process table exceeds safety limit")
            roots,root_unresolved=self._job_roots()
            if root_unresolved:
                raise RuntimeError("durable job process provenance is incomplete")
        except Exception as exc:
            reason=type(exc).__name__
            return {
                "physical_process_safety_resolved":0,
                "physical_process_blocker_count":-1,
                "physical_process_residual_job_count":-1,
                "physical_process_declared_long_lived_count":-1,
                "physical_process_observed_at_ms":observed,
                "physical_process_snapshot_sha256":_canonical_sha256({"error":reason}),
                "physical_process_blocker_summary_json":"[]",
                "physical_process_error":reason[:120],
            }

        by_pid={int(p["pid"]):p for p in processes}
        children={}
        for p in processes:
            children.setdefault(int(p.get("ppid") or 0),[]).append(p)

        blockers={}
        active_tree_count=0
        terminal_residual_count=0

        for root in roots:
            seed=int(root["pid"])
            queue=[seed]
            seen=set()
            members=[]
            while queue:
                parent=queue.pop(0)
                if parent in seen:
                    continue
                seen.add(parent)
                direct=[]
                proc=by_pid.get(parent)
                if proc is not None and self._within_job_window(proc,root):
                    direct.append(proc)
                for child in children.get(parent,[]):
                    if self._within_job_window(child,root):
                        direct.append(child)
                for item in direct:
                    pid=int(item["pid"])
                    if pid not in {int(x["pid"]) for x in members}:
                        members.append(item)
                    for grand in children.get(pid,[]):
                        if self._within_job_window(grand,root):
                            queue.append(pid)
                            break

            if root["state"] in _TERMINAL:
                for proc in members:
                    key=("terminal_job_process_tree",int(proc["pid"]))
                    blockers[key]={
                        "source":"terminal_job_process_tree",
                        "pid":int(proc["pid"]),
                        "ppid":int(proc.get("ppid") or 0),
                        "name":str(proc.get("name") or "")[:120],
                        "proxy_job_id":root["proxy_job_id"],
                    }
                terminal_residual_count+=len(members)
            else:
                active_tree_count+=len(members)

        declarations=self._parse_declarations()
        declared_count=0
        self_pid=os.getpid()
        for proc in processes:
            if int(proc["pid"])==self_pid:
                continue
            # A node watchdog repeats the declared research pattern in its own
            # -DeclaredLongLivedProcessPatterns argument. It is infrastructure,
            # not a worker; ignore only an exact PowerShell -File watchdog launch.
            cmdline=str(proc.get("command_line") or "")
            name=str(proc.get("name") or "").casefold()
            if (name in {"powershell.exe","pwsh.exe"} and re.search(
                r'(?:^|\s)-File\s+(?:"[^"]*[\\/]Watch-RemoteMCP-Node\.ps1"|[^\s"]*[\\/]Watch-RemoteMCP-Node\.ps1)(?:\s|$)',
                cmdline,re.IGNORECASE,
            )):
                continue
            hay=(str(proc.get("name") or "")+" "+cmdline).casefold()
            for idx,pattern in enumerate(declarations):
                if pattern.casefold() in hay:
                    key=("declared_long_lived",int(proc["pid"]))
                    if key not in blockers:
                        blockers[key]={
                            "source":"declared_long_lived",
                            "pid":int(proc["pid"]),
                            "ppid":int(proc.get("ppid") or 0),
                            "name":str(proc.get("name") or "")[:120],
                            "declaration_index":idx,
                        }
                        declared_count+=1
                    break

        ordered=sorted(
            blockers.values(),
            key=lambda x:(str(x.get("source")),int(x.get("pid") or 0)),
        )
        summary=ordered[:64]
        summary_json=json.dumps(
            summary,sort_keys=True,separators=(",",":"),ensure_ascii=False
        )
        digest=_canonical_sha256({
            "blockers":ordered,
            "active_tree_count":active_tree_count,
            "declaration_count":len(declarations),
        })
        return {
            "physical_process_safety_resolved":1,
            "physical_process_blocker_count":len(ordered),
            "physical_process_residual_job_count":terminal_residual_count,
            "physical_process_declared_long_lived_count":declared_count,
            "physical_process_active_job_tree_count":active_tree_count,
            "physical_process_declared_pattern_count":len(declarations),
            "physical_process_observed_at_ms":observed,
            "physical_process_snapshot_sha256":digest,
            "physical_process_blocker_summary_json":summary_json,
            "physical_process_error":"",
        }
