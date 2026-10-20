"""Short-lived one-use OAuth CSRF state, persisted per provider/tenant."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session


def issue_state(db: Session, state_model, user_id: int):
    raw = secrets.token_urlsafe(32)
    db.add(state_model(state_hash=hashlib.sha256(raw.encode()).hexdigest(),
                       user_id=user_id,
                       expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.commit()
    return raw


def consume_state(db: Session, state_model, raw: str) -> int:
    if not raw or len(raw) > 200:
        raise HTTPException(400, "Estado OAuth inválido.")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    # Atomic DELETE RETURNING avoids OAuth state replay between concurrent callbacks.
    user_id = db.execute(
        delete(state_model)
        .where(state_model.state_hash == digest,
               state_model.expires_at > datetime.now(timezone.utc))
        .returning(state_model.user_id)
    ).scalar_one_or_none()
    db.commit()
    if user_id is None:
        raise HTTPException(400, "Autorização expirada ou já utilizada.")
    return user_id
