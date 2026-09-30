"""Fencing untrusted text inside a prompt.

Untrusted text (a user's words, a summary, a note from the study vault) is placed
between ``<<<NAME>>>`` and ``<<<END NAME>>>`` markers and the model is told it is
data. A run of three or more ``<`` or ``>`` inside that text could close the fence
early and start giving instructions, so such runs are replaced by a space first.
"""

from __future__ import annotations

import re
from typing import Final

FENCE_RUN: Final = re.compile(r"<{3,}|>{3,}")


def strip_fence_runs(text: str) -> str:
    """Return *text* with every run of three or more ``<`` or ``>`` replaced by a space."""
    return FENCE_RUN.sub(" ", text)


def fence(name: str, text: str) -> str:
    """Wrap *text* in a named fence, after removing anything that could break out of it.

    Args:
        name: The fence label, for example ``SOURCE S1`` or ``QUESTION``. Trusted.
        text: The untrusted text.

    Returns:
        ``<<<name>>>``, the cleaned text, then ``<<<END name>>>``, on separate lines.
    """
    return f"<<<{name}>>>\n{strip_fence_runs(text)}\n<<<END {name}>>>"
