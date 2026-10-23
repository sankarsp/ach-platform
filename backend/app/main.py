from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import files, iso20022, ops, parties, payments, rdfi, reference, sandbox
from .config import get_settings
from .db import Base, SessionLocal, get_engine
from .services.common import DomainError
from .services.scheduler import run_forever
from .services.seed import seed

log = logging.getLogger("ach")
DEFAULT_VAULT_KEY = "q0dS2Z2wQ6pq7ZQd9qJ0b1QH9rWm3yJ8Rk4xT6uV2sA="


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    s = get_settings()
    if s.seed_demo_data and s.is_sandbox:
        db = SessionLocal()
        try:
            result = seed(db)
            db.commit()
            if result.get("seeded"):
                log.warning("Seeded sandbox data. Demo API keys: %s", ", ".join(result["api_keys"]))
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if not s.is_sandbox and s.vault_key == DEFAULT_VAULT_KEY:
        raise RuntimeError("ACH_VAULT_KEY must be set to a unique Fernet key outside sandbox")
    init_db()
    stop = asyncio.Event()
    task = asyncio.create_task(run_forever(stop)) if s.scheduler_enabled else None
    yield
    stop.set()
    if task:
        await task


app = FastAPI(
    title="ACH Platform API",
    version="1.0.0",
    description="Compliance-first ACH origination and receipt: Nacha rules catalog, FedACH windows and Fed "
                "holiday calendar, sanctions/fraud/AML screening, double-entry ledger, returns, NOCs, "
                "reconciliation and ITSM monitoring. Authenticate with `Authorization: Bearer <api key>`.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(DomainError)
async def domain_error(_: Request, exc: DomainError):
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "env": get_settings().env}


for r in (reference.router, parties.router, payments.router, files.router, iso20022.router, rdfi.router, ops.router,
          sandbox.router):
    app.include_router(r)
