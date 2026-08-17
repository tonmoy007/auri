"""Live-configurable settings, overriding config.py's boot-time Settings()."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    """A single key/value override, editable at runtime via the admin dashboard.

    ``key`` matches a ``Settings`` field name (e.g. ``"OLLAMA_MODEL"``) or,
    for voice masks, ``"VOICE_MASK_<NAME>"`` with a JSON-encoded SoX effect
    list as the value. Absence of a row means "use the ``.env``/``Settings()``
    default" — see ``app.services.settings_service``.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
