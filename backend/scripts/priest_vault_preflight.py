"""Preflight for the study vault: the gate that runs before any note is synced.

Usage: ``python backend/scripts/priest_vault_preflight.py <religion-study dir>``

Prints counts only and never a path, a note title or any matched text, so a leaked
key or address cannot end up in a terminal log or CI output. Exits 1 when something
that must not be sent is present, 2 on a usage error, 0 when the vault is clean:

* a ``.md`` file in a denied location (tool folders, hidden folders, ``assets/``);
* any symlink, to a file or a folder, or a ``.md`` entry that is not a regular file
  (a FIFO or a device, which would hang a read);
* a secret-literal assignment (quoted, or unquoted with a long value), a known API
  token prefix (``sk-``, ``ghp_``, ``github_pat_``, ``AKIA``, ``xoxb-``, ``AIza``) or a
  private key;
* an email address or a phone number (a Bangladesh mobile number is caught even
  when written without separators);
* a note whose frontmatter has no ``type:``.

Files that are not ``.md`` are never sent by the sync script, so they are only counted.
A note whose frontmatter does not parse is counted too; the builder excludes it.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.priest.vault_rules import (
    MARKDOWN_SUFFIX,
    FrontmatterError,
    is_denied_path,
    scalar_text,
    split_frontmatter,
)

# AGENTS.md section 12: a quoted literal of 8+ characters, not a bare identifier.
SECRET_LITERAL: Final = re.compile(
    r"(api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE
)
# The same names unquoted (``OPENAI_API_KEY=...``, ``token: ...``): only a long run of
# non-space characters counts, so "password: hidden" and prose stay clean.
_UNQUOTED_SECRET: Final = re.compile(
    r"\b[\w-]*(?:api[_-]?key|token|secret|passw(?:or)?d)[\w-]*[ \t]*[:=][ \t]*"
    r"(?![\[\"'`])[^\s\"'`]{16,}",
    re.IGNORECASE,
)
# Well-known credential shapes, flagged wherever they appear.
_TOKEN_PREFIXES: Final = re.compile(
    r"(?<![\w-])(?:"
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[bp]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r")(?![\w-])"
)
PRIVATE_KEY: Final = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY[A-Z ]*-----")
EMAIL: Final = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
_PHONE_CANDIDATE: Final = re.compile(
    r"(?<![\w.:/-])\+?\(?\d[\d ()\-]{7,22}\d\)?(?![\w-])"
)
# A Bangladesh mobile number with no separators: 01[3-9]xxxxxxxx, or with 880 / +880.
_BD_MOBILE: Final = re.compile(
    r"(?<![\w.:/+-])(?:\+?880|0)1[3-9]\d{8}(?!\d)|\+8801[3-9]\d{8}(?!\d)"
)
_ISBN_LABEL: Final = re.compile(r"isbn[\s:#-]*$", re.IGNORECASE)
_LABEL_WINDOW: Final = 12
# A separator between digit groups; a bracket wrapped around one number is not one.
_INNER_SEPARATOR: Final = re.compile(r"\d[ \-]+\d|\)\s*\d|\d\s*\(")
_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_MIN_PHONE_DIGITS: Final = 9
_MAX_PHONE_DIGITS: Final = 15

_VIOLATIONS: Final = (
    "denied_paths",
    "symlinks",
    "special_files",
    "secret_literals",
    "private_keys",
    "emails",
    "phones",
    "missing_type",
)
_INFO: Final = ("notes_scanned", "skipped_files", "bad_frontmatter")


@dataclass(frozen=True)
class PreflightReport:
    """How many of each thing the scan found. Counts only, never content."""

    notes_scanned: int = 0
    skipped_files: int = 0
    bad_frontmatter: int = 0
    denied_paths: int = 0
    symlinks: int = 0
    special_files: int = 0
    secret_literals: int = 0
    private_keys: int = 0
    emails: int = 0
    phones: int = 0
    missing_type: int = 0

    @property
    def violation_total(self) -> int:
        """The number of findings that block a sync."""
        return sum(getattr(self, name) for name in _VIOLATIONS)

    @property
    def ok(self) -> bool:
        """Whether the vault may be synced."""
        return self.violation_total == 0

    def lines(self) -> list[str]:
        """One ``name=count`` line per counter, then the verdict."""
        counted = [f"{name}={getattr(self, name)}" for name in (*_INFO, *_VIOLATIONS)]
        return [*counted, "preflight: " + ("PASS" if self.ok else "FAIL")]


def _merged_count(spans: Sequence[tuple[int, int]]) -> int:
    """How many findings remain once overlapping spans are merged into one."""
    count = 0
    end = -1
    for start, stop in sorted(spans):
        if start >= end:
            count += 1
            end = stop
        else:
            end = max(end, stop)
    return count


def _is_isbn(text: str, start: int) -> bool:
    """Whether the number starting at *start* is labelled as an ISBN."""
    return _ISBN_LABEL.search(text[max(0, start - _LABEL_WINDOW) : start]) is not None


def _phone_spans(text: str) -> list[tuple[int, int]]:
    """Spans of phone-like numbers: separated groups, plus bare Bangladesh mobiles."""
    spans: list[tuple[int, int]] = []
    for match in _PHONE_CANDIDATE.finditer(text):
        token = match.group()
        digits = sum(ch.isdigit() for ch in token)
        separated = token.startswith("+") or _INNER_SEPARATOR.search(token) is not None
        in_range = _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS
        if in_range and separated and not _ISO_DATE.fullmatch(token):
            spans.append(match.span())
    spans.extend(m.span() for m in _BD_MOBILE.finditer(text))
    return [span for span in spans if not _is_isbn(text, span[0])]


def count_phones(text: str) -> int:
    """Count phone-like numbers while leaving verse references, years and ids alone."""
    return _merged_count(_phone_spans(text))


def count_secrets(text: str) -> int:
    """Count secret literals: quoted or unquoted assignments and known token shapes."""
    spans = [
        m.span()
        for pattern in (SECRET_LITERAL, _UNQUOTED_SECRET, _TOKEN_PREFIXES)
        for m in pattern.finditer(text)
    ]
    return _merged_count(spans)


def _scan_text(text: str) -> dict[str, int]:
    """Counts of secrets, keys, emails and phones in one note's text."""
    return {
        "secret_literals": count_secrets(text),
        "private_keys": len(PRIVATE_KEY.findall(text)),
        "emails": len(EMAIL.findall(text)),
        "phones": count_phones(text),
    }


def _frontmatter_problem(text: str) -> str | None:
    """``bad_frontmatter`` if the YAML fails, ``missing_type`` if no type, else None."""
    try:
        frontmatter, _body = split_frontmatter(text)
    except FrontmatterError:
        return "bad_frontmatter"
    return None if scalar_text(frontmatter.get("type")).strip() else "missing_type"


def _dir_entries(
    dirpath: str, prefix: str, names: Sequence[str], files: Sequence[str]
) -> Iterator[tuple[str, str, bool]]:
    """Files, and symlinks of any kind, in one directory, as relative/full/is_link."""
    for name in names:
        full = os.path.join(dirpath, name)
        is_link = os.path.islink(full)
        if is_link or name in files:
            yield f"{prefix}{name}", full, is_link


def _entries(root: str) -> Iterator[tuple[str, str, bool]]:
    """Every entry below *root* as ``(relative path, full path, is_symlink)``."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else f"{rel_dir}/"
        yield from _dir_entries(dirpath, prefix, (*dirnames, *filenames), filenames)


def _bump(counts: dict[str, int], more: dict[str, int]) -> None:
    """Add *more* into *counts*."""
    for key, value in more.items():
        counts[key] = counts.get(key, 0) + value


def _is_regular(full_path: str) -> bool:
    """Whether *full_path* is a regular file itself, found without following a link."""
    try:
        return stat.S_ISREG(os.lstat(full_path).st_mode)
    except OSError:
        return False


def _scan_note(full_path: str) -> dict[str, int]:
    """Counts for one in-scope note; a note that is not a regular file is a finding."""
    if not _is_regular(full_path):
        return {"special_files": 1}
    text = Path(full_path).read_bytes().decode("utf-8", errors="replace")
    found = {"notes_scanned": 1, **_scan_text(text)}
    problem = _frontmatter_problem(text)
    if problem:
        found[problem] = 1
    return found


def run_preflight(root: Path) -> PreflightReport:
    """Scan the vault at *root* and return the counts.

    Args:
        root: The ``religion-study`` folder.
    """
    counts: dict[str, int] = {}
    for rel_path, full_path, is_link in _entries(os.fspath(root)):
        if is_link:
            _bump(counts, {"symlinks": 1})
        elif not rel_path.endswith(MARKDOWN_SUFFIX):
            _bump(counts, {"skipped_files": 1})
        elif is_denied_path(rel_path):
            _bump(counts, {"denied_paths": 1})
        else:
            _bump(counts, _scan_note(full_path))
    return PreflightReport(**counts)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the preflight from the command line and return the exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not Path(args[0]).is_dir():
        sys.stderr.write(
            "usage: priest_vault_preflight.py <vault dir>: not a directory\n"
        )
        return 2
    report = run_preflight(Path(args[0]))
    sys.stdout.write("\n".join(report.lines()) + "\n")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
