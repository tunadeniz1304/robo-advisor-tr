"""User accounts and roles (JWT authentication).

Roles:
    * ``musteri``  — sees and acts only on its own customer record.
    * ``danisman`` — manages assigned customers and approves proposals.
    * ``admin``    — full access.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base
from models.base import utcnow


class User(Base):
    """ORM model of the ``users`` table."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="musteri")
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<User id={self.id} username={self.username!r} role={self.role}>"
