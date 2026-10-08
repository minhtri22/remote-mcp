from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from remotemcp.durable.errors import DurableError


def validate_origin(value:str)->str:
    origin=value.rstrip("/")
    p=urlparse(origin)
    if not p.scheme or not p.hostname:
        raise DurableError("INVALID_ARGUMENT","node origin must be absolute URL")
    if p.scheme=="https":
        return origin
    if p.scheme=="http":
        try:
            host=ipaddress.ip_address(p.hostname)
            if host.is_loopback:return origin
        except ValueError:
            if p.hostname=="localhost":return origin
    raise DurableError("INVALID_ARGUMENT","node origin must use HTTPS except loopback")


@dataclass(frozen=True)
class NodeConfig:
    origin:str
    root:Path
    runtime_dir:Path
    legacy_roots:tuple[Path,...]=()
    heartbeat_seconds:int=15
    poll_wait_seconds:int=25

    @classmethod
    def create(cls,origin:str,root:Path,runtime_dir:Path,legacy_roots=()):
        root=root.resolve();runtime_dir=runtime_dir.resolve()
        root.mkdir(parents=True,exist_ok=True);runtime_dir.mkdir(parents=True,exist_ok=True)
        resolved=[]
        seen={str(root).casefold()}
        for raw in legacy_roots or ():
            p=Path(raw).resolve()
            key=str(p).casefold()
            if key in seen:
                continue
            if not p.is_dir():
                raise DurableError(
                    "LEGACY_ROOT_NOT_FOUND",
                    "approved legacy compatibility root does not exist",
                    path=str(p),
                )
            resolved.append(p);seen.add(key)
        return cls(validate_origin(origin),root,runtime_dir,tuple(resolved))
