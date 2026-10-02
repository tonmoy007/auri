"""A small builder for counselor replies, shared by tests that mock ``counsel()``."""

from __future__ import annotations

from app.schemas.counsel import CounselReply, CounselTone


def make_counsel_reply(text: str = "You have been heard.") -> CounselReply:
    """A valid reply whose acknowledgement is *text*."""
    return CounselReply(
        acknowledgement=text,
        reflection="That took courage to say.",
        closing="You are not alone in this.",
        tone=CounselTone.warm,
    )
