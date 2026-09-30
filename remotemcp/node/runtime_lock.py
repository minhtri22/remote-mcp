from __future__ import annotations

import os
from pathlib import Path

from remotemcp.durable.errors import DurableError


class RuntimeLock:
    def __init__(self,runtime_dir:Path):
        self.path=runtime_dir.resolve()/"node.lock"
        self.handle=None

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        h=open(self.path,"a+b")
        try:
            h.seek(0)
            if os.name=="nt":
                import msvcrt
                try:
                    msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1)
                except OSError as exc:
                    raise DurableError("NODE_ALREADY_RUNNING","node runtime is already locked") from exc
            else:
                import fcntl
                try:fcntl.flock(h.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError as exc:raise DurableError("NODE_ALREADY_RUNNING","node runtime is already locked") from exc
            h.seek(0);h.truncate();h.write(str(os.getpid()).encode());h.flush()
            self.handle=h
            return self
        except Exception:
            h.close();raise

    def release(self):
        if not self.handle:return
        try:
            if os.name=="nt":
                import msvcrt
                self.handle.seek(0)
                try:msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
                except OSError:pass
            else:
                import fcntl
                try:fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
                except OSError:pass
        finally:
            self.handle.close();self.handle=None

    def __enter__(self):return self.acquire()
    def __exit__(self,*_):self.release()
