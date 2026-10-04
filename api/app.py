"""
Rasta — web API.

The map does not need this service. District bundles are static files served beside the
page, so the whole visual product works with this process asleep. What lives here is the
agent pipeline: the natural-language question, the written brief, and the verifier.

That split is deliberate. The free tier sleeps after fifteen minutes and takes most of a
minute to wake, and nothing a judge looks at first should depend on that.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# config imports nothing from the package, so loading .env through it is safe here -
# and it must happen before rasta.ai is imported, because that freezes the model name.
from rasta import config  # noqa: E402

config.load_env()

from rasta import ai, bundles, pipeline  # noqa: E402

ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://arahmanmdmajid-rasta-school-access.static.hf.space,"
        "http://127.0.0.1:8780,http://localhost:8780",
    ).split(",") if o.strip()
]

app = FastAPI(
    title="Rasta API",
    version="1.0",
    description="Walking access to school in Pakistan, answered by a verified agent pipeline.",
)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

# ---------------------------------------------------------------- rate limiting
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request, bucket: str, per_minute: int) -> None:
    ip = (request.headers.get("x-forwarded-for")
          or (request.client.host if request.client else "?")).split(",")[0].strip()
    key, now = f"{bucket}:{ip}", time.time()
    q = _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(429, "Too many requests - wait a minute and try again.")
    q.append(now)


# --------------------------------------------------------------------- schemas
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    district: str | None = Field(default=None, max_length=20)


class TraceStep(BaseModel):
    agent: str
    kind: str
    via: str
    decided: str


class AskResponse(BaseModel):
    answer: str
    facts: str | None = None
    caveat: str | None = None
    tag: str | None = None
    district: str | None = None
    explainer: str | None = None
    verified: bool | None = None
    needs_district: bool | None = None
    draw: dict | None = None
    trace: list[TraceStep] = []
    timing_ms: int | None = None


# ---------------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """<!doctype html><meta charset="utf-8"><title>Rasta API</title>
<body style="font:16px/1.6 system-ui;max-width:40rem;margin:3rem auto;padding:0 1rem">
<h1>Rasta API</h1>
<p>The agent pipeline behind <a href="https://arahmanmdmajid-rasta-school-access.static.hf.space">Rasta</a>.
The map itself needs no server — this answers questions about it.</p>
<ul>
<li><code>GET /health</code></li>
<li><code>GET /districts</code></li>
<li><code>GET /district/{code}</code></li>
<li><code>POST /ask</code> — <code>{"question": "where should we open centres?"}</code></li>
</ul>
<p><a href="/docs">Interactive docs</a></p>"""


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "ai": ai.available(),
        "model": ai.MODEL,
        "districts": len(bundles.codes()),
        "threshold_min": config.THRESHOLD_MIN,
    }


@app.get("/districts")
def districts() -> dict:
    return bundles.index()


@app.get("/district/{code}")
def district(code: str) -> dict:
    bundle = bundles.load(code)
    if bundle is None:
        # Named explicitly rather than a bare 404: an off-script judge clicking a
        # district we did not precompute should learn that, not hit a blank.
        raise HTTPException(
            404,
            f"{code} is not bundled in this build. The pipeline runs on any of Pakistan's "
            f"160 districts; {len(bundles.codes())} are precomputed here.",
        )
    return bundle


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request) -> dict:
    rate_limit(request, "ask", 20)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Ask a question.")
    try:
        return pipeline.answer(question, body.district)
    except Exception as exc:
        # The forked code had no wrapper here, so any analysis error became a bare 500
        # with a stack trace. Fail with something a user can read.
        print(f"[ask] failed: {type(exc).__name__}: {exc}")
        raise HTTPException(502, "The analysis failed on that question. Try rephrasing it.")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
