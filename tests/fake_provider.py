"""A fake OpenAI-compatible provider for tests.

Behaviour is chosen by the model name so one server can exercise every path:
  ok-model       200 with usage
  slow-model     sleeps then answers
  slow2-model    sleeps exactly 2s (LOAD_FIX latency tests)
  fail-model     500
  ratelimit-model 429 with Retry-After
  bad-model      400
  empty-model    200 with no choices
  no-usage-model 200 without a usage block
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

fake = FastAPI()
CALLS: list[dict] = []
ACTIVE = 0  # calls currently parked inside the handler, for LOAD_FIX tests


def _body(model: str, text: str = "hello from fake") -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


@fake.post("/v1/chat/completions")
async def completions(request: Request):
    global ACTIVE
    payload = await request.json()
    model = payload.get("model", "")
    CALLS.append({"model": model, "payload": payload, "auth": request.headers.get("authorization")})

    if model == "fail-model":
        return JSONResponse({"error": {"message": "upstream exploded"}}, status_code=500)
    if model == "ratelimit-model":
        return JSONResponse(
            {"error": {"message": "slow down"}}, status_code=429, headers={"Retry-After": "30"}
        )
    if model == "bad-model":
        return JSONResponse({"error": {"message": "bad input"}}, status_code=400)
    if model == "auth-model":
        return JSONResponse({"error": {"message": "invalid key"}}, status_code=401)
    if model == "empty-model":
        return JSONResponse({"id": "x", "choices": []}, status_code=200)
    if model == "slow-model":
        await asyncio.sleep(3)
        return JSONResponse(_body(model, "slow reply"))
    if model in ("slow2-model", "park-model"):
        # Used by the LOAD_FIX regressions: long enough that many requests are
        # parked inside the provider call at the same time.
        ACTIVE += 1
        try:
            await asyncio.sleep(2)
        finally:
            ACTIVE -= 1
        return JSONResponse(_body(model, "slow2 reply"))
    if model == "no-usage-model":
        body = _body(model)
        body.pop("usage")
        return JSONResponse(body)
    return JSONResponse(_body(model))


@fake.get("/v1/models")
async def models():
    return {"data": [{"id": "ok-model"}, {"id": "listed-model"}]}


@fake.get("/__calls")
async def calls():
    return {"calls": CALLS, "active": ACTIVE}


@fake.post("/__reset")
async def reset():
    CALLS.clear()
    return {"ok": True}


def dump_calls() -> str:
    return json.dumps(CALLS)
