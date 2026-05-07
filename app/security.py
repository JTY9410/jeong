from __future__ import annotations

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def ensure_default_user(db: Session, username: str, password: str) -> None:
    existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if existing:
        return
    try:
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()

