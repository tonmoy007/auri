"""Tests for the index builder.

The embedding server is faked with ``httpx.MockTransport`` behind the real
``OllamaEmbedder``, so the incremental behaviour is tested through the real client and
nothing touches the network, Ollama or the real vault. The vault is a writable copy of
the synthetic fixture vault. Because the builder must never put note text into a
status, a manifest or a log, a recurring check is that a marker planted in a note body
never shows up anywhere the builder writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
from app.exceptions import PriestIndexError
from app.priest import build_status, index_builder
from app.priest.build_status import BuildState
from app.priest.embedder import EmbedModelInfo, OllamaEmbedder
from app.priest.index_builder import build_index
from app.priest.index_store import ActiveIndex, list_versions

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "priest_vault"
BASE_URL = "http://ollama.test:11434"
DIM = 768
MARKER = "zebra-quasar-marker"
VERSION_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
# The fixture vault: five indexed notes (11 chunks) and three excluded ones.
FIXTURE_NOTES = 5
FIXTURE_CHUNKS = 11
VIRTUE = "concepts/Sample Virtue.md"


class FakeOllama:
    """A scriptable Ollama that records every document text it is asked to embed."""

    def __init__(
        self,
        *,
        digest: str = "sha256:d1",
        embed_status: int = 200,
        query_sign: float = 1.0,
    ) -> None:
        self.digest = digest
        self.embed_status = embed_status
        self.query_sign = query_sign
        self.embedded: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer ``/api/show`` and ``/api/embed``; anything else is a 404."""
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"digest": self.digest})
        if request.url.path != "/api/embed":
            return httpx.Response(404)
        if self.embed_status != 200:
            return httpx.Response(self.embed_status)
        inputs = json.loads(request.content)["input"]
        self.embedded.extend(t for t in inputs if t.startswith("search_document: "))
        return httpx.Response(
            200, json={"embeddings": [self._vector(text) for text in inputs]}
        )

    def _vector(self, text: str) -> list[float]:
        """A positive vector per text; queries flip sign when ``query_sign`` is -1."""
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        vector = np.abs(np.random.default_rng(seed).normal(size=DIM)) + 0.01
        if text.startswith("search_query: "):
            vector = vector * self.query_sign
        return vector.tolist()

    def embedder(self, cache_dir: Path | None = None) -> OllamaEmbedder:
        """A real embedder whose HTTP client is this fake."""
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return OllamaEmbedder(
            BASE_URL, "nomic-embed-text", client=client, cache_dir=cache_dir
        )


class StepClock:
    """A clock that moves forward one second each time it is read."""

    def __init__(self) -> None:
        self._now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A writable copy of the synthetic vault."""
    target = tmp_path / "vault"
    shutil.copytree(FIXTURE_VAULT, target)
    return target


@pytest.fixture
def clock() -> StepClock:
    """A fresh clock, shared by every build in one test so versions differ."""
    return StepClock()


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    """An empty index root."""
    return tmp_path / "index"


def _build(
    vault: Path, index_dir: Path, fake: FakeOllama, clock: StepClock | None = None
) -> build_status.BuildStatus:
    return build_index(vault, index_dir, fake.embedder(), now=clock or StepClock())


def _active_version(index_dir: Path) -> str:
    return (index_dir / "ACTIVE").read_text().strip()


def _edit_virtue(vault: Path) -> None:
    path = vault / VIRTUE
    path.write_text(path.read_text() + "\n\n## Added\n\nA brand new paragraph here.\n")


# ── A successful build ──────────────────────────────────────────────────


def test_a_first_build_activates_a_version_named_by_time_and_content(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama()

    # Act
    status = _build(vault, index_dir, fake)

    # Assert
    version = _active_version(index_dir)
    assert VERSION_RE.match(version)
    assert status.state is BuildState.succeeded and status.error_code is None
    assert (status.notes_indexed, status.chunks) == (FIXTURE_NOTES, FIXTURE_CHUNKS)
    assert ActiveIndex(index_dir).get().manifest.version == version


def test_the_returned_status_is_what_was_written_to_disk(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama()

    # Act
    status = _build(vault, index_dir, fake)

    # Assert
    assert build_status.read(index_dir) == status
    assert status.pid == os.getpid()
    assert status.finished_at is not None and status.started_at is not None


def test_the_manifest_records_the_model_and_the_indexed_notes_only(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama(digest="sha256:pinned")

    # Act
    _build(vault, index_dir, fake)

    # Assert
    manifest = ActiveIndex(index_dir).get().manifest
    assert (manifest.embed_model, manifest.embed_digest, manifest.dim) == (
        "nomic-embed-text",
        "sha256:pinned",
        DIM,
    )
    assert manifest.note_count == FIXTURE_NOTES
    assert VIRTUE in manifest.note_hashes
    assert "Sample MOC.md" not in manifest.note_hashes


def test_exclusions_are_counted_per_reason(vault: Path, index_dir: Path) -> None:
    # Arrange
    fake = FakeOllama()

    # Act
    status = _build(vault, index_dir, fake)

    # Assert
    expected = {"type:moc": 1, "type:redirect": 1, "planned": 1}
    assert dict(status.exclusions) == expected
    assert ActiveIndex(index_dir).get().manifest.exclusions == expected
    assert status.notes_total == FIXTURE_NOTES + 3


def test_the_same_content_gives_the_same_version_suffix(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama()
    clock = StepClock()
    _build(vault, index_dir, fake, clock)
    first = _active_version(index_dir)

    # Act
    _build(vault, index_dir, fake, clock)

    # Assert
    second = _active_version(index_dir)
    assert second != first and second.split("-")[1] == first.split("-")[1]


def test_an_edit_changes_the_version_suffix(
    vault: Path, index_dir: Path, clock: StepClock
) -> None:
    # Arrange
    fake = FakeOllama()
    _build(vault, index_dir, fake, clock)
    before = _active_version(index_dir).split("-")[1]
    _edit_virtue(vault)

    # Act
    _build(vault, index_dir, fake, clock)

    # Assert
    assert _active_version(index_dir).split("-")[1] != before


def test_a_build_in_the_same_second_with_the_same_content_reuses_the_version(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama()
    frozen = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
    first = build_index(vault, index_dir, fake.embedder(), now=lambda: frozen)
    before = _active_version(index_dir)

    # Act
    second = build_index(vault, index_dir, fake.embedder(), now=lambda: frozen)

    # Assert
    assert first.state is BuildState.succeeded
    assert second.state is BuildState.succeeded
    assert _active_version(index_dir) == before


def test_old_versions_are_pruned_to_three(vault: Path, index_dir: Path) -> None:
    # Arrange
    fake = FakeOllama()
    clock = StepClock()

    # Act
    for _ in range(5):
        _build(vault, index_dir, fake, clock)

    # Assert
    kept = list_versions(index_dir)
    assert len(kept) == 3
    assert _active_version(index_dir) == kept[-1]


def test_progress_reports_stages_and_counts_only(vault: Path, index_dir: Path) -> None:
    # Arrange
    fake = FakeOllama()
    seen: list[tuple[str, int, int]] = []

    # Act
    build_index(
        vault,
        index_dir,
        fake.embedder(),
        now=StepClock(),
        progress=lambda stage, done, total: seen.append((stage, done, total)),
    )

    # Assert
    assert {stage for stage, _, _ in seen} >= {"notes", "embedding", "activating"}
    assert ("embedding", FIXTURE_CHUNKS, FIXTURE_CHUNKS) in seen


# ── Incremental builds ──────────────────────────────────────────────────


def test_a_second_run_re_embeds_nothing(
    vault: Path, index_dir: Path, clock: StepClock
) -> None:
    # Arrange
    fake = FakeOllama()
    _build(vault, index_dir, fake, clock)
    first_count = len(fake.embedded)

    # Act
    status = _build(vault, index_dir, fake, clock)

    # Assert
    assert first_count == FIXTURE_CHUNKS
    assert len(fake.embedded) == first_count
    assert status.chunks == FIXTURE_CHUNKS


def test_an_edited_note_re_embeds_only_its_own_chunks(
    vault: Path, index_dir: Path, clock: StepClock
) -> None:
    # Arrange
    fake = FakeOllama()
    _build(vault, index_dir, fake, clock)
    fake.embedded.clear()
    _edit_virtue(vault)

    # Act
    status = _build(vault, index_dir, fake, clock)

    # Assert
    assert status.chunks == FIXTURE_CHUNKS + 1
    assert fake.embedded
    assert all(t.startswith("search_document: Sample Virtue") for t in fake.embedded)


def test_a_new_embedding_model_digest_re_embeds_everything(
    vault: Path, index_dir: Path, clock: StepClock
) -> None:
    # Arrange
    _build(vault, index_dir, FakeOllama(digest="sha256:old"), clock)
    changed = FakeOllama(digest="sha256:new")

    # Act
    _build(vault, index_dir, changed, clock)

    # Assert
    assert len(changed.embedded) == FIXTURE_CHUNKS


def test_the_embedder_cache_spares_a_changed_note_its_unchanged_chunks(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama()
    cache = index_dir / "embed_cache"
    build_index(vault, index_dir, fake.embedder(cache), now=StepClock())
    (index_dir / "ACTIVE").unlink()
    fake.embedded.clear()

    # Act
    status = build_index(vault, index_dir, fake.embedder(cache), now=StepClock())

    # Assert
    assert status.state is BuildState.succeeded
    assert fake.embedded == []


# ── Failures leave the active index alone ───────────────────────────────


def test_a_failed_build_leaves_the_active_index_untouched(
    vault: Path, index_dir: Path, clock: StepClock
) -> None:
    # Arrange
    _build(vault, index_dir, FakeOllama(), clock)
    before = _active_version(index_dir)
    versions = list_versions(index_dir)
    _edit_virtue(vault)

    # Act
    status = _build(vault, index_dir, FakeOllama(embed_status=500), clock)

    # Assert
    assert status.state is BuildState.failed
    assert status.error_code == "embedder_unreachable"
    assert _active_version(index_dir) == before
    assert list_versions(index_dir) == versions
    assert build_status.read(index_dir).error_code == "embedder_unreachable"


def test_a_missing_vault_fails_with_its_code(tmp_path: Path, index_dir: Path) -> None:
    # Arrange
    fake = FakeOllama()

    # Act
    status = _build(tmp_path / "nowhere", index_dir, fake)

    # Assert
    assert status.state is BuildState.failed and status.error_code == "vault_missing"
    assert not (index_dir / "ACTIVE").exists()


def test_a_vault_with_nothing_to_index_fails_with_no_chunks(
    tmp_path: Path, index_dir: Path
) -> None:
    # Arrange
    empty = tmp_path / "only-hubs"
    empty.mkdir()
    (empty / "Hub.md").write_text("---\ntype: moc\n---\n\n# Hub\n")

    # Act
    status = _build(empty, index_dir, FakeOllama())

    # Assert
    assert status.error_code == "no_chunks"
    assert dict(status.exclusions) == {"type:moc": 1}


def test_a_smoke_query_with_no_hits_fails_validation_and_activates_nothing(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    fake = FakeOllama(query_sign=-1.0)

    # Act
    status = _build(vault, index_dir, fake)

    # Assert
    assert status.error_code == "validation_failed"
    assert not (index_dir / "ACTIVE").exists()
    assert list_versions(index_dir) == []


def test_a_failed_activation_removes_the_version_it_wrote(
    vault: Path, index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    def refuse(root: Path, version: str) -> None:
        raise PriestIndexError("index version does not exist")

    monkeypatch.setattr(index_builder.index_store, "activate", refuse)

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.error_code == "validation_failed"
    assert list_versions(index_dir) == []


def test_an_unforeseen_error_becomes_the_unexpected_code(
    vault: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"boom {MARKER}")

    monkeypatch.setattr(index_builder, "clean_note", explode)

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.failed and status.error_code == "unexpected"
    assert MARKER not in json.dumps(status.to_json()) + caplog.text
    assert "RuntimeError" in caplog.text


# ── Hostile notes ───────────────────────────────────────────────────────


def test_a_note_over_the_size_budget_is_left_out_not_fatal(
    vault: Path, index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr("app.priest.vault_rules.MAX_NOTE_BYTES", 20_000)
    (vault / "concepts" / "Huge.md").write_text(
        "---\ntype: concept\n---\n" + "word " * 5000, encoding="utf-8"
    )

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.succeeded
    assert dict(status.exclusions)["too_large"] == 1
    assert status.notes_indexed == FIXTURE_NOTES


@pytest.mark.parametrize("error", [RecursionError, MemoryError])
def test_a_note_the_cleaner_cannot_process_is_left_out_not_fatal(
    vault: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: type[BaseException],
) -> None:
    # Arrange: the cleaner gives up on one note only
    real = index_builder.clean_note

    def picky(rel_path: str, text: str, targets: object) -> object:
        if rel_path == VIRTUE:
            raise error("too much")
        return real(rel_path, text, targets)  # type: ignore[arg-type]

    monkeypatch.setattr(index_builder, "clean_note", picky)

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.succeeded
    assert dict(status.exclusions)["unprocessable"] == 1
    assert status.notes_indexed == FIXTURE_NOTES - 1
    assert VIRTUE not in ActiveIndex(index_dir).get().manifest.note_hashes


def test_deeply_nested_frontmatter_leaves_out_that_note_only(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    (vault / "concepts" / "Deep.md").write_text(
        "---\ntitle: " + "[" * 3000 + "]" * 3000 + "\n---\nBody", encoding="utf-8"
    )

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.succeeded
    assert dict(status.exclusions)["bad_frontmatter"] == 1
    assert status.notes_indexed == FIXTURE_NOTES


def test_a_note_with_an_unclosed_code_fence_is_counted_in_a_manifest_warning(
    vault: Path, index_dir: Path
) -> None:
    # Arrange: a stray ~~~ hides everything after it, so the build says so
    (vault / "concepts" / "Fenced.md").write_text(
        "---\ntype: concept\n---\nKept text.\n\n~~~\nlost " + MARKER + "\n",
        encoding="utf-8",
    )

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert: a count only, never the note's name or text
    assert status.state is BuildState.succeeded
    manifest = ActiveIndex(index_dir).get().manifest
    assert "unclosed_fences=1" in manifest.warnings
    assert "Fenced" not in json.dumps(manifest.warnings)


def test_a_vault_with_no_unclosed_fence_adds_no_such_warning(
    vault: Path, index_dir: Path
) -> None:
    # Act
    _build(vault, index_dir, FakeOllama())

    # Assert
    warnings = ActiveIndex(index_dir).get().manifest.warnings
    assert not any(w.startswith("unclosed_fences") for w in warnings)


# ── The lock ────────────────────────────────────────────────────────────


def test_a_held_lock_refuses_and_leaves_everything_as_it_was(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    assert build_status.acquire_lock(index_dir, pid=os.getpid())
    running = build_status.BuildStatus(state=BuildState.running, pid=os.getpid())
    build_status.write(index_dir, running)

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.failed and status.error_code == "already_running"
    assert build_status.read(index_dir) == running
    assert build_status.lock_owner(index_dir) == os.getpid()
    assert list_versions(index_dir) == []


def test_the_lock_is_released_after_a_success_and_after_a_failure(
    vault: Path, index_dir: Path, clock: StepClock
) -> None:
    # Arrange
    _build(vault, index_dir, FakeOllama(), clock)
    after_success = build_status.lock_owner(index_dir)

    # Act
    _build(vault, index_dir, FakeOllama(embed_status=500, digest="sha256:x"), clock)

    # Assert
    assert after_success is None
    assert build_status.lock_owner(index_dir) is None


def test_a_dead_builders_lock_is_taken_over(vault: Path, index_dir: Path) -> None:
    # Arrange
    index_dir.mkdir()
    (index_dir / build_status.LOCK_FILE).write_text("2147483646")

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.succeeded


def test_stale_staging_directories_are_cleared(vault: Path, index_dir: Path) -> None:
    # Arrange
    stale = index_dir / "versions" / ".tmp-crashed"
    stale.mkdir(parents=True)
    (stale / "chunks.jsonl").write_text("{}")
    kept = index_dir / "versions" / "20260101T000000Z-aaaaaaaa"
    kept.mkdir()

    # Act
    status = _build(vault, index_dir, FakeOllama())

    # Assert
    assert status.state is BuildState.succeeded
    assert not stale.exists()
    assert kept.exists()


# ── No text anywhere the builder writes ─────────────────────────────────


def test_no_note_text_reaches_a_status_a_manifest_or_a_log(
    vault: Path, index_dir: Path, clock: StepClock, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange
    note = vault / "concepts" / "Marker Note.md"
    note.write_text(
        f"---\ntype: concept\n---\n\n# Heading\n\nBody {MARKER} [[Gone]].\n"
    )
    caplog.set_level(logging.DEBUG)

    # Act
    ok = _build(vault, index_dir, FakeOllama(), clock)
    failed = _build(
        vault, index_dir, FakeOllama(embed_status=500, digest="sha256:y"), clock
    )

    # Assert
    manifest_text = (
        index_dir / "versions" / _active_version(index_dir) / "manifest.json"
    ).read_text()
    written = json.dumps([ok.to_json(), failed.to_json()])
    status_text = (index_dir / build_status.BUILD_STATUS_FILE).read_text()
    assert ok.state is BuildState.succeeded and failed.state is BuildState.failed
    assert MARKER not in written + status_text + manifest_text + caplog.text
    assert "Body" not in manifest_text + status_text + caplog.text


def test_unresolved_links_are_counted_not_quoted(vault: Path, index_dir: Path) -> None:
    # Arrange
    note = vault / "concepts" / "Linker.md"
    note.write_text("---\ntype: concept\n---\n\n# Linker\n\nSee [[Nowhere Land]].\n")

    # Act
    _build(vault, index_dir, FakeOllama())

    # Assert
    manifest = ActiveIndex(index_dir).get().manifest
    assert manifest.unresolved_links >= 1
    assert "Nowhere" not in json.dumps(manifest.warnings)


# ── The command line ────────────────────────────────────────────────────


def _run_cli(
    vault: Path,
    index: Path,
    fake: FakeOllama,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    monkeypatch.setattr(index_builder.settings, "PRIEST_VAULT_DIR", str(vault))
    monkeypatch.setattr(index_builder.settings, "PRIEST_INDEX_DIR", str(index))
    return index_builder.main([], embedder_factory=lambda cache: fake.embedder(cache))


def test_the_command_exits_zero_and_prints_counts_only(
    vault: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    fake = FakeOllama()

    # Act
    code = _run_cli(vault, index_dir, fake, monkeypatch)

    # Assert
    out = capsys.readouterr().out
    assert code == 0
    assert f"chunks={FIXTURE_CHUNKS}" in out and "state=succeeded" in out
    assert "Sample" not in out and "Virtue" not in out


def test_the_command_exits_non_zero_with_the_error_code(
    vault: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    fake = FakeOllama(embed_status=500)

    # Act
    code = _run_cli(vault, index_dir, fake, monkeypatch)

    # Assert
    assert code == 1
    assert "error_code=embedder_unreachable" in capsys.readouterr().out


def test_the_command_exits_with_its_own_code_when_a_build_is_running(
    vault: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    assert build_status.acquire_lock(index_dir, pid=os.getpid())

    # Act
    code = _run_cli(vault, index_dir, FakeOllama(), monkeypatch)

    # Assert
    assert code == 3
    assert "error_code=already_running" in capsys.readouterr().out


def test_an_unknown_embedding_model_fails_the_command_and_is_recorded(
    vault: Path,
    index_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    set_setting: Callable[[str, object], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    set_setting("PRIEST_EMBED_MODEL", "not-in-the-registry")
    monkeypatch.setattr(index_builder.settings, "PRIEST_VAULT_DIR", str(vault))
    monkeypatch.setattr(index_builder.settings, "PRIEST_INDEX_DIR", str(index_dir))

    # Act
    code = index_builder.main([])

    # Assert
    assert code == 1
    assert "error_code=embedder_unreachable" in capsys.readouterr().out
    assert build_status.read(index_dir).state is BuildState.failed
    assert build_status.lock_owner(index_dir) is None


def test_the_default_embedder_uses_the_configured_model_and_cache_dir(
    index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr(index_builder.settings, "OLLAMA_BASE_URL", BASE_URL)
    seen: dict[str, object] = {}

    class Spy:
        def __init__(self, base: str, model: str, *, cache_dir: Path | None) -> None:
            seen.update(base=base, model=model, cache_dir=cache_dir)

    monkeypatch.setattr(index_builder, "OllamaEmbedder", Spy)

    # Act
    index_builder._default_embedder(index_dir / "embed_cache")

    # Assert
    assert seen["base"] == BASE_URL
    assert seen["model"] == index_builder.priest_config.embed_model()
    assert seen["cache_dir"] == index_dir / "embed_cache"


# ── Embedder failures that are not HTTP ─────────────────────────────────


class DownEmbedder:
    """An embedder that cannot be reached at all."""

    async def model_info(self) -> EmbedModelInfo:
        raise PriestIndexError("embedding server is unreachable")

    async def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        raise PriestIndexError("embedding server is unreachable")

    async def embed_query(self, text: str) -> np.ndarray:
        raise PriestIndexError("embedding server is unreachable")


def test_an_unreachable_embedder_is_reported_before_any_work(
    vault: Path, index_dir: Path
) -> None:
    # Arrange
    embedder = DownEmbedder()

    # Act
    status = build_index(vault, index_dir, embedder, now=StepClock())

    # Assert
    assert status.error_code == "embedder_unreachable"
    assert build_status.read(index_dir).state is BuildState.failed
