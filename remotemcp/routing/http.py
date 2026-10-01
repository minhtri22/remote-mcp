from __future__ import annotations

import json
from .node_bundle import build_node_bundle

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from remotemcp.durable.errors import DurableError


def _status(code:str)->int:
    if code in {"DEVICE_SIGNATURE_INVALID","DEVICE_SIGNATURE_STALE"}:
        return 401
    if code in {
        "DEVICE_REPLAY","DEVICE_ROUTE_GENERATION_MISMATCH","DEVICE_REVOKED",
        "DEVICE_OFFLINE","COMMAND_CONFLICT","DEVICE_PAIRING_EXPIRED",
        "DEVICE_PAIRING_INVALID",
    }:
        return 409
    if code in {"DEVICE_NOT_FOUND","NOT_FOUND"}:
        return 404
    return 400


def _error(exc:Exception):
    if isinstance(exc,DurableError):
        return JSONResponse(
            {"error_code":exc.code,"message":exc.message,"details":exc.details},
            status_code=_status(exc.code),
        )
    return JSONResponse(
        {"error_code":"INTERNAL_ERROR","message":str(exc)},
        status_code=500,
    )


async def _body(request:Request,max_bytes:int)->tuple[bytes,dict]:
    raw=await request.body()
    if len(raw)>max_bytes:
        raise DurableError("INVALID_ARGUMENT","request body too large",max_bytes=max_bytes)
    try:
        payload=json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        raise DurableError("INVALID_ARGUMENT","malformed JSON body") from exc
    if not isinstance(payload,dict):
        raise DurableError("INVALID_ARGUMENT","JSON body must be object")
    return raw,payload


def register_device_routes(mcp,service):
    @mcp.custom_route("/device/v1/node-bundle.zip",methods=["GET"])
    async def node_bundle(_request:Request):
        try:
            return Response(
                build_node_bundle(),
                media_type="application/zip",
                headers={"Cache-Control":"no-store","Content-Disposition":"attachment; filename=remotemcp-node.zip"},
            )
        except Exception as exc:
            return _error(exc)

    @mcp.custom_route("/device/v1/join/{pairing_id}/{ticket}",methods=["GET"])
    async def join_script(request:Request):
        try:
            script=service.pairing.join_script(
                request.path_params["pairing_id"],
                request.path_params["ticket"],
            )
            return Response(
                script,
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control":"no-store"},
            )
        except Exception as exc:
            return _error(exc)

    @mcp.custom_route("/device/v1/pair",methods=["POST"])
    async def pair(request:Request):
        try:
            _,payload=await _body(request,service.config.max_pair_body_bytes)
            return JSONResponse(service.pair_http(payload))
        except Exception as exc:
            return _error(exc)

    @mcp.custom_route("/device/v1/heartbeat",methods=["POST"])
    async def heartbeat(request:Request):
        try:
            raw,payload=await _body(request,service.config.max_signed_body_bytes)
            device=service.verify_signed("POST",request.url.path,request.headers,raw)
            return JSONResponse(service.heartbeat_http(device,payload))
        except Exception as exc:
            return _error(exc)

    @mcp.custom_route("/device/v1/poll",methods=["POST"])
    async def poll(request:Request):
        try:
            raw,_=await _body(request,service.config.max_signed_body_bytes)
            device=service.verify_signed("POST",request.url.path,request.headers,raw)
            envelope=await service.poll_http(device)
            if envelope is None:
                return Response(status_code=204)
            return JSONResponse(envelope)
        except Exception as exc:
            return _error(exc)

    @mcp.custom_route("/device/v1/commands/{command_id}/result",methods=["POST"])
    async def result(request:Request):
        try:
            raw,payload=await _body(request,service.config.max_signed_body_bytes)
            device=service.verify_signed("POST",request.url.path,request.headers,raw)
            return JSONResponse(
                service.result_http(device,request.path_params["command_id"],payload)
            )
        except Exception as exc:
            return _error(exc)

    return mcp
