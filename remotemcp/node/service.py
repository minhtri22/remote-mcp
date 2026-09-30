from __future__ import annotations

import asyncio

from .cas import NodeCas
from .command_journal import NodeCommandJournal
from .db import NodeDatabase
from .executor import NodeExecutor
from .identity import NodeIdentity
from .jobs import NodeJobs
from .projects import NodeProjects
from .runtime_lock import RuntimeLock
from .worktrees import NodeWorktrees


class NodeService:
    def __init__(self,config,client_factory=None):
        self.config=config
        self.lock=RuntimeLock(config.runtime_dir)
        self.db=NodeDatabase(config.runtime_dir);self.db.bootstrap()
        self.identity=NodeIdentity(config.runtime_dir,self.db)
        self.projects=NodeProjects(self.db,config.root)
        self.worktrees=NodeWorktrees(self.db,config.root,self.projects)
        self.journal=NodeCommandJournal(self.db)
        self.jobs=NodeJobs(config,self.db,self.identity,self.projects,self.worktrees)
        self.cas=NodeCas(self.db,self.worktrees,self.journal)
        self.executor=NodeExecutor(config,self.journal,self.projects,self.worktrees,self.cas,self.jobs)
        from .client import NodeClient
        self.client=(client_factory or NodeClient)(config,self.identity)
        self._stop=asyncio.Event()

    async def start(self):
        self.lock.acquire()
        self.cas.reconcile_all()
        await self.jobs.start()

    async def stop(self):
        self._stop.set()
        await self.jobs.stop()
        await self.client.close()
        self.lock.release()

    async def run_forever(self):
        if not self.identity.paired:
            raise RuntimeError("node must be paired before run")
        await self.start()
        try:
            last_hb=0.0
            loop=asyncio.get_running_loop()
            while not self._stop.is_set():
                now=loop.time()
                if now-last_hb>=self.config.heartbeat_seconds:
                    active=sum(1 for r in self.db.query_all("SELECT state FROM node_routed_jobs") if r["state"] not in ("SUCCEEDED","FAILED","CANCELLED","LOST"))
                    await self.client.heartbeat(active)
                    last_hb=now
                envelope=await self.client.poll()
                if envelope is None:
                    continue
                result=await self.executor.execute(envelope)
                await self.client.result(envelope["command_id"],result)
        finally:
            await self.stop()
