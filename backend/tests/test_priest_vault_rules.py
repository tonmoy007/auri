"""Tests for the vault allowlist, exclusion rules, tradition map and note iterator.

The vault holds tool debris next to the notes (plugin configs that can hold API keys,
scripts, images, a sibling folder of scriptures), so these rules decide what may ever
leave the laptop or be indexed. Everything here uses the synthetic fixture vault.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import threading
import unicodedata
from collections.abc import Mapping
from pathlib import Path

import pytest
from app.priest.schemas import TraditionId
from app.priest.vault_rules import (
    TOO_LARGE,
    TRADITION_MAP,
    FrontmatterError,
    VaultNote,
    exclusion_reason,
    is_denied_path,
    iter_vault_notes,
    scalar_text,
    split_frontmatter,
    tags_of,
    traditions_for,
)

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "priest_vault"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A writable copy of the synthetic vault, so a test can add debris to it."""
    target = tmp_path / "religion-study"
    shutil.copytree(FIXTURE_VAULT, target)
    return target


# -- is_denied_path -----------------------------------------------------------


@pytest.mark.parametrize(
    ("rel_path", "reason"),
    [
        (".obsidian/x", "tool_dir"),
        (".obsidian/plugins/copilot/notes.md", "tool_dir"),
        ("connections/.omc/state/y.json", "tool_dir"),
        ("concepts/.claude/n.md", "tool_dir"),
        (".smart-env/a.md", "tool_dir"),
        (".claudian/a.md", "tool_dir"),
        (".git/config", "tool_dir"),
        (".OBSIDIAN/x.md", "tool_dir"),
        ("sub/.hidden/n.md", "hidden"),
        (".DS_Store", "hidden"),
        ("stories/.DS_Store", "hidden"),
        ("a.py", "not_markdown"),
        ("scripts/run.sh", "not_markdown"),
        ("concepts/notes.MD", "not_markdown"),
        ("noextension", "not_markdown"),
        ("assets/p.jpg", "assets"),
        ("assets/readme.md", "assets"),
        ("concepts/assets/notes.md", "assets"),
        ("../scriptures/z.md", "path_traversal"),
        ("concepts/../../z.md", "path_traversal"),
        ("/etc/passwd.md", "outside_scope"),
        ("C:/x.md", "outside_scope"),
        ("concepts\\x.md", "outside_scope"),
        ("", "outside_scope"),
        ("concepts/\x00x.md", "outside_scope"),
    ],
)
def test_denied_paths_are_refused_with_a_reason(rel_path: str, reason: str) -> None:
    # Arrange / Act
    result = is_denied_path(rel_path)

    # Assert
    assert result == reason


@pytest.mark.parametrize(
    "rel_path",
    [
        "Root Note.md",
        "concepts/Sample Virtue.md",
        "stories/buddhist/Ahiṃsā.md",
        "concepts/Notes about assets.md",
        "concepts/a.b.md",
    ],
)
def test_ordinary_notes_are_allowed(rel_path: str) -> None:
    # Arrange / Act
    result = is_denied_path(rel_path)

    # Assert
    assert result is None


# -- exclusion_reason ---------------------------------------------------------


@pytest.mark.parametrize(
    "note_type", ["redirect", "moc", "index", "scripture-index", "scripture-library"]
)
def test_navigation_note_types_are_excluded(note_type: str) -> None:
    # Arrange
    frontmatter = {"type": note_type, "tags": ["religion-study"]}

    # Act
    result = exclusion_reason(frontmatter)

    # Assert
    assert result == f"type:{note_type}"


def test_excluded_type_match_ignores_case_and_spaces() -> None:
    # Arrange
    frontmatter = {"type": " MOC "}

    # Act
    result = exclusion_reason(frontmatter)

    # Assert
    assert result == "type:moc"


@pytest.mark.parametrize(
    "frontmatter",
    [
        {"type": "concept", "tags": ["planned", "hinduism"]},
        {"type": "concept", "tags": ["#Planned"]},
        {"type": "concept", "tags": "planned"},
        {"type": "concept", "depth": "planned"},
        {"type": "concept", "depth": "Planned "},
    ],
)
def test_planned_stubs_are_excluded(frontmatter: Mapping[str, object]) -> None:
    # Act
    result = exclusion_reason(frontmatter)

    # Assert
    assert result == "planned"


@pytest.mark.parametrize(
    "frontmatter",
    [
        {"type": "concept", "tags": ["islam"]},
        {"type": "story", "depth": "academic"},
        {"type": "figure", "tags": None},
        {"type": 7, "tags": [3, None]},
        {},
    ],
)
def test_real_notes_are_not_excluded(frontmatter: Mapping[str, object]) -> None:
    # Act
    result = exclusion_reason(frontmatter)

    # Assert
    assert result is None


def test_type_exclusion_wins_over_planned() -> None:
    # Arrange
    frontmatter = {"type": "redirect", "tags": ["planned"]}

    # Act
    result = exclusion_reason(frontmatter)

    # Assert
    assert result == "type:redirect"


# -- traditions_for -----------------------------------------------------------


def test_tradition_map_only_holds_known_tradition_ids() -> None:
    # Arrange
    known = {member.value for member in TraditionId}

    # Act
    mapped = {value for values in TRADITION_MAP.values() for value in values}

    # Assert
    assert mapped <= known
    assert known <= mapped


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["islam"], ("islam",)),
        (["#Islam", "religion-study"], ("islam",)),
        (["religion/zoroastrianism"], ("zoroastrianism",)),
        (["quran", "hadith"], ("islam",)),
        (["bible", "new-testament"], ("christianity",)),
        (["old-testament"], ("judaism", "christianity")),
        (["mishnah", "buddha"], ("judaism", "buddhism")),
        (["figure", "ethics"], ()),
        ([], ()),
    ],
)
def test_traditions_come_from_tags(tags: list[str], expected: tuple[str, ...]) -> None:
    # Act
    result = traditions_for(tags, "concepts/Some Note.md")

    # Assert
    assert result == expected


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("stories/buddhist/A.md", ("buddhism",)),
        ("stories/jain/A.md", ("jainism",)),
        ("stories/sikh/A.md", ("sikhism",)),
        ("stories/zoroastrian/A.md", ("zoroastrianism",)),
        ("stories/jewish/A.md", ("judaism",)),
        ("stories/mesopotamian/A.md", ("mesopotamian",)),
        ("islamic-concepts/A.md", ("islam",)),
        ("stories/A.md", ()),
        ("concepts/A.md", ()),
    ],
)
def test_traditions_come_from_the_folder(
    rel_path: str, expected: tuple[str, ...]
) -> None:
    # Act
    result = traditions_for([], rel_path)

    # Assert
    assert result == expected


def test_traditions_merge_tags_and_folder_without_duplicates_in_a_fixed_order() -> None:
    # Arrange
    tags = ["buddha", "hinduism", "buddhism"]

    # Act
    result = traditions_for(tags, "stories/buddhist/A.md")

    # Assert
    assert result == ("hinduism", "buddhism")


# -- split_frontmatter --------------------------------------------------------


def test_frontmatter_is_split_from_the_body() -> None:
    # Arrange
    text = "---\ntitle: T\ntags:\n  - a\n---\n\n# Body\n"

    # Act
    frontmatter, body = split_frontmatter(text)

    # Assert
    assert frontmatter == {"title": "T", "tags": ["a"]}
    assert body.strip() == "# Body"


def test_crlf_frontmatter_and_a_byte_order_mark_are_handled() -> None:
    # Arrange
    text = "\ufeff---\r\ntype: concept\r\n---\r\nBody\r\n"

    # Act
    frontmatter, body = split_frontmatter(text)

    # Assert
    assert frontmatter == {"type": "concept"}
    assert body.strip() == "Body"


def test_a_note_without_frontmatter_has_an_empty_mapping() -> None:
    # Act
    frontmatter, body = split_frontmatter("# Just a heading\n")

    # Assert
    assert frontmatter == {}
    assert body == "# Just a heading\n"


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: [unclosed\n---\nBody",
        "---\n- just\n- a list\n---\nBody",
        "---\ntitle: never closed\nBody",
        "---\nkey: a: b: c\n---\n",
    ],
)
def test_malformed_frontmatter_raises(text: str) -> None:
    # Act / Assert
    with pytest.raises(FrontmatterError):
        split_frontmatter(text)


# -- iter_vault_notes ---------------------------------------------------------


def test_iterator_yields_only_allowed_notes_in_a_fixed_order(vault: Path) -> None:
    # Arrange
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "notes.md").write_text("---\ntype: concept\n---\nx")
    (vault / "assets" / "concepts").mkdir(parents=True)
    (vault / "assets" / "concepts" / "p.jpg").write_bytes(b"\xff\xd8")
    (vault / "connections" / ".omc" / "state").mkdir(parents=True)
    (vault / "connections" / ".omc" / "state" / "y.json").write_text("{}")
    (vault / "download.py").write_text("print('x')")

    # Act
    rel_paths = [note.rel_path for note in iter_vault_notes(vault)]

    # Assert
    assert rel_paths == sorted(rel_paths)
    assert rel_paths == [
        "Sample MOC.md",
        "concepts/Old Name.md",
        "concepts/Planned Stub.md",
        "concepts/Poisoned Note.md",
        "concepts/Sample Virtue.md",
        "figures/Sample Figure.md",
        "stories/buddhist/Sample Story.md",
        "texts/Sample Text.md",
    ]


def test_iterator_fills_hash_text_frontmatter_and_exclusion(vault: Path) -> None:
    # Arrange
    path = vault / "concepts" / "Sample Virtue.md"
    raw = path.read_bytes()

    # Act
    notes = {note.rel_path: note for note in iter_vault_notes(vault)}
    note = notes["concepts/Sample Virtue.md"]

    # Assert
    assert note.sha256 == hashlib.sha256(raw).hexdigest()
    assert note.text == raw.decode("utf-8")
    assert note.frontmatter["type"] == "concept"
    assert note.exclusion_reason is None


def test_iterator_marks_redirects_mocs_and_stubs_as_excluded(vault: Path) -> None:
    # Act
    notes = {note.rel_path: note for note in iter_vault_notes(vault)}

    # Assert
    assert notes["concepts/Old Name.md"].exclusion_reason == "type:redirect"
    assert notes["Sample MOC.md"].exclusion_reason == "type:moc"
    assert notes["concepts/Planned Stub.md"].exclusion_reason == "planned"


def test_iterator_marks_unparseable_frontmatter_as_bad(vault: Path) -> None:
    # Arrange
    (vault / "concepts" / "Broken.md").write_text("---\ntitle: [oops\n---\nBody")

    # Act
    notes = {note.rel_path: note for note in iter_vault_notes(vault)}

    # Assert
    assert notes["concepts/Broken.md"].exclusion_reason == "bad_frontmatter"
    assert notes["concepts/Broken.md"].frontmatter == {}


def test_iterator_marks_undecodable_files_instead_of_crashing(vault: Path) -> None:
    # Arrange
    (vault / "concepts" / "Latin1.md").write_bytes(b"---\ntype: concept\n---\n\xe9\xe8")

    # Act
    notes = {note.rel_path: note for note in iter_vault_notes(vault)}

    # Assert
    assert notes["concepts/Latin1.md"].exclusion_reason == "bad_encoding"


def test_iterator_never_follows_symlinks(vault: Path, tmp_path: Path) -> None:
    # Arrange
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Secret.md").write_text("---\ntype: concept\n---\nprivate")
    (vault / "linked-dir").symlink_to(outside, target_is_directory=True)
    (vault / "concepts" / "Linked.md").symlink_to(outside / "Secret.md")

    # Act
    rel_paths = [note.rel_path for note in iter_vault_notes(vault)]

    # Assert
    assert "linked-dir/Secret.md" not in rel_paths
    assert "concepts/Linked.md" not in rel_paths
    assert len(rel_paths) == 8


# -- hostile frontmatter --------------------------------------------------------


def _alias_bomb(levels: int = 5, width: int = 9) -> str:
    """YAML whose aliases expand to ``width ** levels`` items if anyone prints them."""
    lines = ["a0: &a0 [" + ", ".join(["x"] * width) + "]"]
    for n in range(1, levels):
        refs = ", ".join([f"*a{n - 1}"] * width)
        lines.append(f"a{n}: &a{n} [{refs}]")
    return "\n".join(lines)


def test_deeply_nested_frontmatter_is_refused_not_a_crash() -> None:
    # Arrange
    text = "---\ntitle: " + "[" * 3000 + "]" * 3000 + "\n---\nBody"

    # Act / Assert
    with pytest.raises(FrontmatterError):
        split_frontmatter(text)


def test_oversized_frontmatter_is_refused() -> None:
    # Arrange
    text = "---\ntitle: " + "x" * 200_000 + "\n---\nBody"

    # Act / Assert
    with pytest.raises(FrontmatterError):
        split_frontmatter(text)


def test_an_alias_bomb_in_tags_is_ignored_without_being_expanded() -> None:
    # Arrange
    text = "---\n" + _alias_bomb() + "\ntags: *a4\ntype: *a4\n---\nBody"
    frontmatter, _body = split_frontmatter(text)

    # Act / Assert
    assert tags_of(frontmatter) == []
    assert exclusion_reason(frontmatter) is None


def test_tags_that_are_not_plain_scalars_are_dropped() -> None:
    # Arrange
    frontmatter = {"tags": ["islam", ["nested", "list"], {"a": "b"}, 7, "x" * 500]}

    # Act / Assert
    assert tags_of(frontmatter) == ["islam", "7"]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("Islam", "Islam"),
        (42, "42"),
        (True, "True"),
        (3.5, "3.5"),
        (["a"], ""),
        ({"a": 1}, ""),
        (None, ""),
        ("y" * 5000, ""),
    ],
)
def test_scalar_text_only_stringifies_short_plain_values(
    value: object, text: str
) -> None:
    # Act / Assert
    assert scalar_text(value) == text


# -- unusual files ---------------------------------------------------------------


def _collect_in_thread(vault: Path) -> list[VaultNote] | None:
    """Run the iterator off-thread, so a read that never returns fails the test."""
    found: list[list[VaultNote]] = []
    worker = threading.Thread(
        target=lambda: found.append(list(iter_vault_notes(vault))), daemon=True
    )
    worker.start()
    worker.join(timeout=10)
    return found[0] if found else None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_a_fifo_named_like_a_note_is_skipped_and_never_read(vault: Path) -> None:
    # Arrange
    os.mkfifo(vault / "concepts" / "Pipe.md")

    # Act
    notes = _collect_in_thread(vault)

    # Assert
    assert notes is not None, "the iterator hung on a FIFO"
    assert "concepts/Pipe.md" not in [n.rel_path for n in notes]
    assert len(notes) == 8


def test_a_note_over_the_size_budget_is_excluded_without_being_parsed(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr("app.priest.vault_rules.MAX_NOTE_BYTES", 20_000)
    (vault / "concepts" / "Huge.md").write_text(
        "---\ntype: concept\n---\n" + "word " * 5000, encoding="utf-8"
    )

    # Act
    notes = {n.rel_path: n for n in iter_vault_notes(vault)}

    # Assert
    huge = notes["concepts/Huge.md"]
    assert huge.exclusion_reason == TOO_LARGE == "too_large"
    assert huge.text == ""
    assert notes["concepts/Sample Virtue.md"].exclusion_reason is None


def test_deeply_nested_frontmatter_in_a_note_excludes_only_that_note(
    vault: Path,
) -> None:
    # Arrange
    (vault / "concepts" / "Deep.md").write_text(
        "---\ntitle: " + "[" * 3000 + "]" * 3000 + "\n---\nBody", encoding="utf-8"
    )

    # Act
    notes = {n.rel_path: n for n in iter_vault_notes(vault)}

    # Assert
    assert notes["concepts/Deep.md"].exclusion_reason == "bad_frontmatter"
    assert len(notes) == 9


def test_vault_note_is_frozen(vault: Path) -> None:
    # Arrange
    note = next(iter(iter_vault_notes(vault)))

    # Act / Assert
    assert isinstance(note, VaultNote)
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.text = "changed"  # type: ignore[misc]


def test_iterator_reads_nothing_when_the_root_is_missing(tmp_path: Path) -> None:
    # Act
    notes = list(iter_vault_notes(tmp_path / "missing"))

    # Assert
    assert notes == []


def test_fixture_vault_holds_no_symlinks_or_denied_files() -> None:
    # Arrange
    found = [
        os.path.join(dirpath, name)
        for dirpath, _dirs, names in os.walk(FIXTURE_VAULT)
        for name in names
        if os.path.islink(os.path.join(dirpath, name))
        or is_denied_path(os.path.relpath(os.path.join(dirpath, name), FIXTURE_VAULT))
    ]

    # Assert
    assert found == []


# -- path normalisation (plan 14.6, privacy review row 50) ---------------------

NFD_NAME = "Kisā Gotamī.md"  # "Kisā Gotamī" as macOS may store it
NFC_NAME = unicodedata.normalize("NFC", NFD_NAME)


def test_a_decomposed_file_name_is_reported_in_composed_form(vault: Path) -> None:
    # Arrange
    (vault / "stories" / "buddhist" / NFD_NAME).write_text(
        "---\ntype: story\n---\n# Kisā Gotamī\n\n## The Search\n\nShe walked all day.\n",
        encoding="utf-8",
    )

    # Act
    rel_paths = [note.rel_path for note in iter_vault_notes(vault)]

    # Assert — readable from the decomposed name, reported in the composed one
    assert f"stories/buddhist/{NFC_NAME}" in rel_paths
    assert f"stories/buddhist/{NFD_NAME}" not in rel_paths
