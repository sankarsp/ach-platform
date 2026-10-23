from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ApiClient, Originator
from ..services.common import hash_key


@dataclass
class Principal:
    client: ApiClient

    @property
    def actor(self) -> str:
        return f"{self.client.role}:{self.client.name}"

    @property
    def is_operator(self) -> bool:
        return self.client.role in ("operator", "admin")

    @property
    def originator_id(self) -> str | None:
        return self.client.originator_id


def principal(authorization: str = Header(default=""), x_actor: str | None = Header(default=None),
              db: Session = Depends(get_db)) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, {"code": "unauthorized", "message": "Bearer API key required"})
    key = authorization.split(" ", 1)[1].strip()
    client = db.scalar(select(ApiClient).where(ApiClient.key_hash == hash_key(key), ApiClient.active.is_(True)))
    if client is None:
        raise HTTPException(401, {"code": "unauthorized", "message": "Invalid API key"})
    p = Principal(client)
    if x_actor and p.is_operator:
        # Ops console passes the signed-in analyst so maker-checker and audit record the person, not the key
        p.client = ApiClient(id=client.id, name=x_actor[:60], role=client.role, originator_id=None,
                             key_hash=client.key_hash, key_prefix=client.key_prefix)
    return p


def operator(p: Principal = Depends(principal)) -> Principal:
    if not p.is_operator:
        raise HTTPException(403, {"code": "forbidden", "message": "Operator role required"})
    return p


def admin(p: Principal = Depends(principal)) -> Principal:
    if p.client.role != "admin":
        raise HTTPException(403, {"code": "forbidden", "message": "Admin role required"})
    return p


def scoped_originator(db: Session, p: Principal, originator_id: str | None) -> Originator:
    """Originator clients are pinned to their own originator; operators may act for any."""
    oid = p.originator_id if not p.is_operator else originator_id
    if not oid:
        raise HTTPException(422, {"code": "originator_required", "message": "originator_id is required"})
    if not p.is_operator and originator_id and originator_id != p.originator_id:
        raise HTTPException(403, {"code": "forbidden", "message": "Not your originator"})
    o = db.get(Originator, oid)
    if o is None:
        raise HTTPException(404, {"code": "not_found", "message": "Originator not found"})
    return o
