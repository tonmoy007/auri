"""Tests for the vault preflight and the allowlist sync script.

The preflight is the gate before any note leaves the laptop. It prints counts only, so
a leaked key or address can never end up in a terminal log or CI output, and it exits
non-zero on anything that must not be sent. The sync script is only ever run against a
local directory here; no test reaches a host, the network or the real vault.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "priest_vault_preflight.py"
SYNC_SCRIPT = BACKEND.parent / "scripts" / "priest-vault-sync.sh"
FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "priest_vault"

# Built from pieces so this file never holds a literal the repo's secret scan flags.
SECRET_LINE = "api" + "_key" + ' = "' + "x1y2z3w4" * 2 + '"'
KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
NOTE_HEAD = "---\ntype: concept\ntags:\n  - islam\n---\n\n# Extra\n\n"


def _load_script() -> ModuleType:
    """Import the script by path, as it is not part of a package."""
    spec = importlib.util.spec_from_file_location("priest_vault_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load_script()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A writable copy of the synthetic vault."""
    target = tmp_path / "religion-study"
    shutil.copytree(FIXTURE_VAULT, target)
    return target


def _add_note(vault: Path, body: str, name: str = "Extra.md", head: str = NOTE_HEAD):
    """Write one extra note at the vault root."""
    (vault / name).write_text(head + body, encoding="utf-8")


def test_clean_vault_passes_with_every_count_zero(vault: Path) -> None:
    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.ok
    assert report.notes_scanned == 8
    assert report.violation_total == 0


def test_denied_markdown_inside_tool_directories_fails(vault: Path) -> None:
    # Arrange
    for rel in (".obsidian/n.md", "connections/.omc/state/n.md", "assets/readme.md"):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(NOTE_HEAD, encoding="utf-8")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert not report.ok
    assert report.denied_paths == 3


def test_files_that_the_sync_never_sends_are_counted_not_failed(vault: Path) -> None:
    # Arrange
    (vault / "assets").mkdir()
    (vault / "assets" / "p.jpg").write_bytes(b"\xff\xd8")
    (vault / "a.py").write_text("print('x')")
    (vault / "connections" / ".omc" / "state").mkdir(parents=True)
    (vault / "connections" / ".omc" / "state" / "y.json").write_text("{}")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.ok
    assert report.skipped_files == 3


def test_symlinks_fail_whether_they_point_at_files_or_folders(
    vault: Path, tmp_path: Path
) -> None:
    # Arrange
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Private.md").write_text(NOTE_HEAD, encoding="utf-8")
    (vault / "dir-link").symlink_to(outside, target_is_directory=True)
    (vault / "file-link.md").symlink_to(outside / "Private.md")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert not report.ok
    assert report.symlinks == 2
    assert report.notes_scanned == 8


def test_a_secret_literal_assignment_is_flagged(vault: Path) -> None:
    # Arrange
    _add_note(vault, f"Config was {SECRET_LINE} in an old script.\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert not report.ok
    assert report.secret_literals == 1


def test_a_bare_field_name_is_not_a_secret(vault: Path) -> None:
    # Arrange
    _add_note(
        vault, "The word token is used here. password: hidden. api_key is named.\n"
    )

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.ok


# Fake, synthetic values only: each is assembled from pieces so this file never holds
# a literal the repo's secret scan flags.
_FAKE_TAIL = "abcdefghijklmnop1234"


@pytest.mark.parametrize(
    "line",
    [
        "api_key: " + "sk-" + "proj-" + _FAKE_TAIL,
        "OPENAI" + "_API_KEY=" + "sk-" + _FAKE_TAIL,
        "to" + "ken: " + "x9" * 10,
        "client" + "_secret = " + "Zq7" * 6,
        "pass" + "word: " + "Zq7!" * 5,
    ],
)
def test_unquoted_secret_assignments_with_a_long_value_are_flagged(
    vault: Path, line: str
) -> None:
    # Arrange
    _add_note(vault, f"Old notes.\n{line}\nMore text.\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.secret_literals == 1, line
    assert not report.ok


@pytest.mark.parametrize(
    "token",
    [
        "sk-" + _FAKE_TAIL,
        "ghp" + "_" + "A1" * 18,
        "github" + "_pat_" + "B2" * 15,
        "AK" + "IA" + "ABCDEFGH01234567",
        "xo" + "xb-" + "1234567890-abcdef",
        "xo" + "xp-" + "1234567890-abcdef",
        "AI" + "za" + "Sy" + "C3" * 15,
    ],
)
def test_known_token_prefixes_are_flagged_anywhere(vault: Path, token: str) -> None:
    # Arrange
    _add_note(vault, f"Pasted by mistake: {token} end.\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.secret_literals == 1, token
    assert not report.ok


def test_a_quoted_key_with_a_token_prefix_is_one_finding(vault: Path) -> None:
    # Arrange
    _add_note(vault, 'api_key = "' + "sk-" + _FAKE_TAIL + '"\n')

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.secret_literals == 1


@pytest.mark.parametrize(
    "text",
    [
        "The token: a short word. password: hidden. secret: none.",
        "A token of esteem; the secret of the self; a password for the gate.",
        "token: [[Some Long Linked Note Title]] and the sk-learn library.",
        "Task-list: skip-this-one and ask-me-later are ordinary words.",
    ],
)
def test_ordinary_prose_about_tokens_and_secrets_is_not_flagged(
    vault: Path, text: str
) -> None:
    # Arrange
    _add_note(vault, text + "\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.secret_literals == 0, text
    assert report.ok


def test_a_private_key_block_is_flagged(vault: Path) -> None:
    # Arrange
    _add_note(vault, f"{KEY_HEADER}\nMIIabc\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert not report.ok
    assert report.private_keys == 1


def test_email_addresses_are_flagged(vault: Path) -> None:
    # Arrange
    _add_note(vault, "Write to first.last@example.org or other+tag@mail.example.com.\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert not report.ok
    assert report.emails == 2


@pytest.mark.parametrize(
    "phone",
    [
        "+880 1712-345678",
        "+1 (555) 010-9999",
        "555-123-4567",
        "01712 345678",
        "+8801712345678",
        "01712345678",
        "8801712345678",
        "01912-345678",
    ],
)
def test_phone_numbers_are_flagged(vault: Path, phone: str) -> None:
    # Arrange
    _add_note(vault, f"Call {phone} today.\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.phones == 1, phone
    assert not report.ok


@pytest.mark.parametrize(
    "text",
    [
        "Yasna 44.14 and Quran 2:255 and Matthew 5:3-12.",
        "In c. 2600-2400 BCE, about 1999 years, pp. 100-120.",
        "Born 1879-03-14, created 2026-01-02, section 1.2.3.4.",
        "Genesis 1:1-2:3 and 1234567890 as a bare id.",
        "Image File_Example_(9841650413).jpg and (10162517184) again.",
        "ISBN 9780140449198 and ISBN-10 0134685997 and isbn: 0-13-468599-7.",
        "Verse ids 0171234567 and 017123456789 and 2026010217123 are ids.",
        "Sura 01712345 and page 1712345678 of the book.",
    ],
)
def test_verse_references_dates_and_ids_are_not_phones(vault: Path, text: str) -> None:
    # Arrange
    _add_note(vault, text + "\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.phones == 0, text
    assert report.ok


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_named_like_a_note_fails_and_is_never_read(vault: Path) -> None:
    # Arrange
    os.mkfifo(vault / "Pipe.md")
    found: list[object] = []
    worker = threading.Thread(
        target=lambda: found.append(preflight.run_preflight(vault)), daemon=True
    )

    # Act: a read of the FIFO would never return, so wait for the answer with a limit
    worker.start()
    worker.join(timeout=10)

    # Assert
    assert found, "the preflight hung on a FIFO"
    report = found[0]
    assert report.special_files == 1  # type: ignore[attr-defined]
    assert not report.ok  # type: ignore[attr-defined]
    assert report.notes_scanned == 8  # type: ignore[attr-defined]


def test_a_list_valued_type_counts_as_missing_without_being_printed(
    vault: Path,
) -> None:
    # Arrange
    _add_note(vault, "Body.\n", head="---\ntype: [a, b]\n---\n\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.missing_type == 1


def test_a_note_without_a_type_is_flagged(vault: Path) -> None:
    # Arrange
    _add_note(vault, "Body.\n", head="---\ntitle: Untyped\n---\n\n")
    _add_note(vault, "Body.\n", name="No Frontmatter.md", head="")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert not report.ok
    assert report.missing_type == 2


def test_unparseable_frontmatter_is_counted_but_does_not_fail(vault: Path) -> None:
    # Arrange
    _add_note(vault, "Body.\n", head="---\ntitle: [oops\n---\n\n")

    # Act
    report = preflight.run_preflight(vault)

    # Assert
    assert report.ok
    assert report.bad_frontmatter == 1
    assert report.missing_type == 0


def test_cli_prints_counts_only_and_exits_non_zero_on_a_violation(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    _add_note(vault, f"Mail me at leaky.person@example.org. {SECRET_LINE}\n")
    _add_note(vault, "Concept Note Secret Sentence.\n", name="Distinct Name.md")

    # Act
    code = preflight.main([str(vault)])

    # Assert
    out = capsys.readouterr().out
    assert code == 1
    assert "emails=1" in out
    assert "secret_literals=1" in out
    assert "FAIL" in out
    for leaked in ("leaky.person", "x1y2z3w4", "Distinct Name", "Secret Sentence"):
        assert leaked not in out
    assert str(vault) not in out


def test_cli_exits_zero_on_a_clean_vault(
    vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = preflight.main([str(vault)])

    # Assert
    assert code == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_rejects_a_missing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = preflight.main([str(tmp_path / "nowhere")])

    # Assert
    assert code == 2
    assert "not a directory" in capsys.readouterr().err


def test_script_runs_standalone_from_any_directory(vault: Path, tmp_path: Path) -> None:
    # Act
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(vault)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    # Assert
    assert done.returncode == 0, done.stderr
    assert "notes_scanned=8" in done.stdout


# -- priest-vault-sync.sh -----------------------------------------------------

needs_rsync = pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("bash") is None,
    reason="rsync and bash are required",
)


def _sync(
    args: list[str], vault: Path, dest: Path | None, **env: str
) -> subprocess.CompletedProcess[str]:
    """Run the sync script against a local destination, with a clean environment."""
    full_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")}
    if dest is not None:
        full_env["PRIEST_VAULT_DIR"] = str(dest)
    full_env.update(env)
    return subprocess.run(
        ["bash", str(SYNC_SCRIPT), *args, str(vault)],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _debris(vault: Path) -> None:
    """Add everything the allowlist must keep off the server."""
    for rel in (
        ".obsidian/data.json",
        "assets/concepts/p.jpg",
        "download.py",
        "connections/.omc/state/y.json",
        "concepts/notes.txt",
    ):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text("debris", encoding="utf-8")


def _files_under(root: Path) -> list[str]:
    """Sorted relative paths of every file below *root*."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@needs_rsync
def test_sync_sends_only_markdown_notes(vault: Path, tmp_path: Path) -> None:
    # Arrange
    _debris(vault)
    dest = tmp_path / "server" / "priest" / "vault"

    # Act
    done = _sync([], vault, dest)

    # Assert
    assert done.returncode == 0, done.stderr
    sent = _files_under(dest)
    assert len(sent) == 8
    assert all(name.endswith(".md") for name in sent)
    assert "concepts/Sample Virtue.md" in sent


@needs_rsync
def test_sync_dry_run_changes_nothing(vault: Path, tmp_path: Path) -> None:
    # Arrange
    dest = tmp_path / "server" / "priest" / "vault"

    # Act
    done = _sync(["--dry-run"], vault, dest)

    # Assert
    assert done.returncode == 0, done.stderr
    assert not dest.exists() or _files_under(dest) == []


@needs_rsync
def test_sync_refuses_when_the_preflight_fails(vault: Path, tmp_path: Path) -> None:
    # Arrange
    _add_note(vault, f"{SECRET_LINE}\n")
    dest = tmp_path / "server" / "priest" / "vault"

    # Act
    done = _sync([], vault, dest)

    # Assert
    assert done.returncode != 0
    assert "preflight" in done.stderr.lower()
    assert not dest.exists()


@needs_rsync
def test_sync_needs_a_destination(vault: Path) -> None:
    # Act
    done = _sync([], vault, None)

    # Assert
    assert done.returncode == 2
    assert "PRIEST_VAULT_DIR" in done.stderr


@needs_rsync
@pytest.mark.parametrize("unsafe", ["/", "relative/dir", "/tmp", "/a/../etc"])
def test_sync_rejects_unsafe_destinations(vault: Path, unsafe: str) -> None:
    # Act
    done = _sync([], vault, None, PRIEST_VAULT_DIR=unsafe)

    # Assert
    assert done.returncode == 2
    assert "PRIEST_VAULT_DIR" in done.stderr


@needs_rsync
def test_sync_prune_removes_only_stale_notes_inside_the_destination(
    vault: Path, tmp_path: Path
) -> None:
    # Arrange
    dest = tmp_path / "server" / "priest" / "vault"
    assert _sync([], vault, dest).returncode == 0
    (dest / "Stale.md").write_text("old", encoding="utf-8")
    (dest / "keep.json").write_text("{}", encoding="utf-8")
    sibling = tmp_path / "server" / "priest" / "index.json"
    sibling.write_text("{}", encoding="utf-8")

    # Act
    done = _sync(["--prune"], vault, dest)

    # Assert
    assert done.returncode == 0, done.stderr
    assert not (dest / "Stale.md").exists()
    assert (dest / "keep.json").exists()
    assert sibling.exists()


@needs_rsync
def test_sync_without_prune_keeps_stale_notes(vault: Path, tmp_path: Path) -> None:
    # Arrange
    dest = tmp_path / "server" / "priest" / "vault"
    assert _sync([], vault, dest).returncode == 0
    (dest / "Stale.md").write_text("old", encoding="utf-8")

    # Act
    done = _sync([], vault, dest)

    # Assert
    assert done.returncode == 0, done.stderr
    assert (dest / "Stale.md").exists()


@needs_rsync
def test_sync_refuses_a_source_that_is_not_the_religion_study_folder(
    vault: Path, tmp_path: Path
) -> None:
    # Arrange
    parent_like = tmp_path / "whole-vault"
    vault.rename(parent_like)
    dest = tmp_path / "server" / "priest" / "vault"

    # Act
    done = _sync([], parent_like, dest)

    # Assert
    assert done.returncode == 2
    assert "religion-study" in done.stderr
    assert not dest.exists()


def _recording_rsync(tmp_path: Path) -> tuple[Path, Path]:
    """A fake rsync first on PATH that records its arguments and sends nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "rsync-args.txt"
    shim = bin_dir / "rsync"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\n', encoding="utf-8")
    shim.chmod(0o755)
    return bin_dir, log


@needs_rsync
def test_sync_to_a_host_uses_an_ssh_rsync_with_the_allowlist_filters(
    vault: Path, tmp_path: Path
) -> None:
    # Arrange
    bin_dir, log = _recording_rsync(tmp_path)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    # Act
    done = _sync(
        ["--dry-run"],
        vault,
        Path("/srv/app/priest/vault"),
        PATH=path,
        PRIEST_SYNC_HOST="deploy@example.test",
        PRIEST_SSH_PORT="2222",
    )

    # Assert
    assert done.returncode == 0, done.stderr
    args = log.read_text(encoding="utf-8").splitlines()
    assert "--dry-run" in args
    assert args[args.index("-e") + 1] == "ssh -o BatchMode=yes -p 2222"
    assert args[-1] == "deploy@example.test:/srv/app/priest/vault/"
    assert args.index("--exclude=.*") < args.index("--include=*.md")
    assert args[-2] == f"{vault}/"
    assert args.index("--exclude=*") > args.index("--include=*.md")
    assert not any(arg.startswith("--delete") for arg in args)


@needs_rsync
def test_sync_prune_never_deletes_excluded_files(vault: Path, tmp_path: Path) -> None:
    # Arrange
    bin_dir, log = _recording_rsync(tmp_path)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    # Act
    done = _sync(["--prune"], vault, tmp_path / "priest" / "vault", PATH=path)

    # Assert
    assert done.returncode == 0, done.stderr
    args = log.read_text(encoding="utf-8").splitlines()
    assert "--delete" in args
    assert "--delete-excluded" not in args


@needs_rsync
@pytest.mark.parametrize(
    "dest", ["a/b", "home/deploy", "srv/priest/vault-old", "srv/priest/vaults"]
)
def test_sync_prune_refuses_a_destination_that_is_not_the_priest_vault(
    vault: Path, tmp_path: Path, dest: str
) -> None:
    # Arrange: --delete on a shared directory would remove every stale *.md in it
    bin_dir, log = _recording_rsync(tmp_path)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    # Act
    done = _sync(["--prune"], vault, tmp_path / dest, PATH=path)

    # Assert: refused before rsync ran
    assert done.returncode == 2
    assert "--prune" in done.stderr and "/priest/vault" in done.stderr
    assert not log.exists()


@needs_rsync
def test_sync_prune_accepts_a_destination_ending_in_the_priest_vault_folder(
    vault: Path, tmp_path: Path
) -> None:
    # Arrange
    bin_dir, log = _recording_rsync(tmp_path)
    path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    # Act
    done = _sync(
        ["--prune", "--dry-run"],
        vault,
        Path("/srv/app/priest/vault/"),
        PATH=path,
        PRIEST_SYNC_HOST="deploy@example.test",
    )

    # Assert
    assert done.returncode == 0, done.stderr
    assert "--delete" in log.read_text(encoding="utf-8").splitlines()


@needs_rsync
def test_sync_without_prune_still_allows_any_destination(
    vault: Path, tmp_path: Path
) -> None:
    # Act
    done = _sync([], vault, tmp_path / "a" / "b")

    # Assert
    assert done.returncode == 0, done.stderr


@needs_rsync
@pytest.mark.parametrize("host", ["-oProxyCommand=x", "host;rm", "a b", "$(x)"])
def test_sync_rejects_a_host_that_could_inject_ssh_options(
    vault: Path, tmp_path: Path, host: str
) -> None:
    # Act
    done = _sync([], vault, tmp_path / "a" / "b", PRIEST_SYNC_HOST=host)

    # Assert
    assert done.returncode == 2
    assert "PRIEST_SYNC_HOST" in done.stderr
