"""Explicit local test relay: real model response hold or injected HTTP 503.

Never generates an answer, rewrites prompts or falls back to another model. The
authorized upstream body/status is forwarded verbatim. Credentials and model
payloads are not logged. A separate random test credential protects the listener.
"""

import argparse
import asyncio
import json
import secrets
from pathlib import Path
from typing import TypedDict

import httpx
import uvicorn
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response


class RelayState(TypedDict):
    mode: str
    held: bool
    completed_upstream_calls: int
    injected_http_failures: int


def create_relay(state_path: Path):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    values = dotenv_values(".env")
    upstream = str(values.get("MODEL_API_BASE") or "").rstrip("/")
    key = str(values.get("MODEL_API_KEY") or "")
    if not key or not upstream.startswith(("http://", "https://")):
        raise ValueError("Explicit authorized model upstream is required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    control: RelayState = {"mode": "pass", "held": False, "completed_upstream_calls": 0, "injected_http_failures": 0}
    released = asyncio.Event()

    def authorize(request):
        value = request.headers.get("authorization", "")
        if not secrets.compare_digest(value, "Bearer " + state["token"]):
            raise HTTPException(401)

    @app.get("/test/state")
    async def get_state(request: Request):
        authorize(request)
        return control

    @app.post("/test/{action}")
    async def command(action: str, request: Request):
        authorize(request)
        if action == "release":
            released.set()
        elif action in {"hold_next", "fail_next", "pass"}:
            if control["held"]:
                raise HTTPException(409, "Release the held actual response first")
            control["mode"] = action
        else:
            raise HTTPException(404)
        return control

    @app.post("/v1/{endpoint:path}")
    async def relay(endpoint: str, request: Request):
        authorize(request)
        if endpoint not in {"responses", "chat/completions"}:
            raise HTTPException(404)
        body = await request.body()
        if len(body) > 200000:
            raise HTTPException(413)
        mode, control["mode"] = control["mode"], "pass"
        if mode == "fail_next":
            control["injected_http_failures"] += 1
            return Response(b'{"error":"explicit local HTTP fault injection"}', status_code=503,
                            media_type="application/json")
        async with httpx.AsyncClient(timeout=90, trust_env=False, follow_redirects=False) as client:
            result = await client.post(upstream + "/" + endpoint, content=body,
                                       headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        control["completed_upstream_calls"] += 1
        if mode == "hold_next":
            released.clear()
            control["held"] = True
            try:
                await asyncio.wait_for(released.wait(), timeout=20)
            finally:
                control["held"] = False
        return Response(result.content, status_code=result.status_code,
                        media_type=result.headers.get("content-type", "application/json"))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18491)
    args = parser.parse_args()
    # Windows Docker Desktop reaches host loopback through host.docker.internal.
    # This is not a public listener and still requires a distinct random token.
    uvicorn.run(create_relay(args.state), host="127.0.0.1", port=args.port,
                access_log=False, log_level="critical")


if __name__ == "__main__":
    main()
