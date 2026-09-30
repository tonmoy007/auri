"""Fencing untrusted text inside a prompt.

Untrusted text (a user's words, a summary, a note from the study vault) is placed
between ``<<<NAME>>>`` and ``<<<END NAME>>>`` markers and the model is told it is
data. A run of three or more ``<`` or ``>`` inside that text could close the fence
early and start giving instructions, so such runs are replaced by a space first.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# Runs of three or more opening or closing angle marks, in ASCII and in the look-alike
# forms (full-width, single and double guillemets, mathematical and CJK brackets).
FENCE_RUN: Final = re.compile(
    "[<\u2039\u00ab\uff1c\ufe64\u226a\u27e8\u3008\u276e\u276c]{3,}"
    "|[>\u203a\u00bb\uff1e\ufe65\u226b\u27e9\u3009\u276f\u276d]{3,}"
)


def strip_fence_runs(text: str) -> str:
    """Return *text* with every run of three or more angle marks replaced by a space.

    Invisible format characters are removed first, so a run cannot be broken up by a
    zero-width character, and look-alike angle marks count as the real ones.
    """
    visible = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return FENCE_RUN.sub(" ", visible)


def fence(name: str, text: str) -> str:
    """Wrap *text* in a named fence, after removing anything that could break out of it.

    Args:
        name: The fence label, for example ``SOURCE S1`` or ``QUESTION``. Trusted.
        text: The untrusted text.

    Returns:
        ``<<<name>>>``, the cleaned text, then ``<<<END name>>>``, on separate lines.
    """
    return f"<<<{name}>>>\n{strip_fence_runs(text)}\n<<<END {name}>>>"
