"""Tests for the priest-mode admin API (task 13.22).

The router is mounted on a small app of its own, so these tests do not depend on
where the owner registers it. Nothing here touches the network, Ollama, a real chat
server, a real vault or a real subprocess: the builder launcher and the health-probe
HTTP client are replaced with fakes, and every index is synthetic under ``tmp_path``.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from app.api.v1 import priest_admin
from app.api.v1.priest_admin import (
    get_builder_launcher,
    get_probe_client,
    router,
)
from app.database import get_async_session
from app.llm.chat_endpoint import ChatEndpoint
from app.models.audit_event import AuditAction, AuditEvent
from app.models.user import UserRole
from app.priest import build_status, index_store, metrics
from app.priest.build_status import BuildState, BuildStatus
from app.priest.types import Chunk
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from tests.conftest import (
    TEST_SESSION_SECRET,
    SettingPatcher,
    StaffFactory,
)
from tests.test_priest_index_store import make_chunk, make_manifest, make_vectors

PREFIX = "/api/v1/admin/priest"
ADMIN_KEY = "legacy-admin-key-for-tests"
VERSION_A = "20260701T100000Z-aaaaaaaa"
VERSION_B = "20260702T100000Z-bbbbbbbb"
CANARY = "CANARY-note-text-7f3a"
PRIMARY_KEY = "sk-primary-secret-for-tests"
REPORT_LIMIT = 64 * 1024


# ── Fakes and fixtures ───────────────────────────────────────────────────


class FakeLauncher:
    """Stands in for the subprocess launcher; counts launches, spawns nothing."""

    def __init__(self) -> None:
        self.calls = 0
        # A pid that is certainly alive, so the status the API writes reads as running.
        self.pid = os.getpid()
        self.error: OSError | None = None

    def __call__(self) -> int:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.pid


class Probes:
    """Records every health-probe request and answers from a table."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status_for: dict[str, int] = {}
        self.down: set[str] = set()
        self.body = b""
        self.location = "http://elsewhere.invalid/"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.url.host}{request.url.path}"
        if key in self.down:
            raise httpx.ConnectError("refused", request=request)
        status = self.status_for.get(key, 200)
        headers = {"location": self.location} if 300 <= status < 400 else {}
        return httpx.Response(status, content=self.body, headers=headers)


@pytest.fixture
def index_dir(tmp_path: Path, set_setting: SettingPatcher) -> Path:
    """An empty index root that the API is configured to use."""
    root = tmp_path / "index"
    root.mkdir()
    set_setting("PRIEST_INDEX_DIR", str(root))
    return root


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def probes() -> Probes:
    return Probes()


@pytest.fixture(autouse=True)
def fresh_metrics() -> None:
    """Start and end every test with empty in-process counts."""
    metrics.reset()


@pytest_asyncio.fixture
async def client(
    db_engine: AsyncEngine,
    set_setting: SettingPatcher,
    index_dir: Path,
    launcher: FakeLauncher,
    probes: Probes,
) -> AsyncIterator[AsyncClient]:
    """A client on an app that mounts only the priest admin router."""
    set_setting("SESSION_TOKEN_SECRET", TEST_SESSION_SECRET)
    set_setting("ADMIN_API_KEY", ADMIN_KEY)
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.commit()

    async def probe_override() -> AsyncIterator[httpx.AsyncClient]:
        async with priest_admin.build_probe_client(
            transport=httpx.MockTransport(probes.handle)
        ) as probe_client:
            yield probe_client

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_async_session] = session_override
    app.dependency_overrides[get_builder_launcher] = lambda: launcher
    app.dependency_overrides[get_probe_client] = probe_override
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        yield api


def legacy() -> dict[str, str]:
    return {"X-Admin-Api-Key": ADMIN_KEY}


def write_index(
    root: Path,
    version: str = VERSION_A,
    *,
    active: bool = True,
    note_hash_key: str = "stories/demo/note-0.md",
    error_code: str | None = None,
    chunk_text: str | None = None,
) -> None:
    """Write a synthetic version (optionally active) under *root*."""
    chunk = make_chunk(0)
    if chunk_text is not None:
        chunk = Chunk(**{**chunk.__dict__, "text": chunk_text})
    manifest = make_manifest(version, 1)
    manifest = type(manifest)(
        **{
            **manifest.__dict__,
            "note_hashes": {note_hash_key: "0" * 64},
            "error_code": error_code,
        }
    )
    index_store.write_version(root, [chunk], make_vectors(1), manifest)
    if active:
        index_store.activate(root, version)


async def audit_rows(db_session: AsyncSession) -> list[AuditEvent]:
    return list((await db_session.execute(select(AuditEvent))).scalars().all())


def dead_pid() -> int:
    """The pid of a process that has exited and been reaped."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


# (method, path suffix, json body, status an administrator gets)
ROUTES: list[tuple[str, str, dict[str, Any] | None, int]] = [
    ("GET", "/index", None, 200),
    ("POST", "/reindex", None, 202),
    ("GET", "/reindex/status", None, 200),
    ("POST", "/activate", {"version": VERSION_A}, 200),
    ("GET", "/health", None, 200),
    ("GET", "/usage", None, 200),
    ("GET", "/report", None, 200),
]
ROUTE_IDS = [f"{m} {p}" for m, p, _, _ in ROUTES]


async def call(
    client: AsyncClient,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.request(method, PREFIX + suffix, json=body, headers=headers)


# ── Role matrix ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "body", "ok"), ROUTES, ids=ROUTE_IDS)
@pytest.mark.parametrize("role", [UserRole.hr, UserRole.moderator])
async def test_non_admin_roles_are_refused_and_nothing_runs(
    client: AsyncClient,
    make_staff: StaffFactory,
    launcher: FakeLauncher,
    index_dir: Path,
    role: UserRole,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    ok: int,
) -> None:
    # Arrange
    write_index(index_dir)
    _, headers = await make_staff(role)

    # Act
    response = await call(client, method, suffix, body, headers)

    # Assert
    assert response.status_code == 403
    assert launcher.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "body", "ok"), ROUTES, ids=ROUTE_IDS)
async def test_admin_session_is_allowed(
    client: AsyncClient,
    make_staff: StaffFactory,
    index_dir: Path,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    ok: int,
) -> None:
    # Arrange
    write_index(index_dir)
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await call(client, method, suffix, body, headers)

    # Assert
    assert response.status_code == ok


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "body", "ok"), ROUTES, ids=ROUTE_IDS)
async def test_legacy_admin_key_is_allowed(
    client: AsyncClient,
    index_dir: Path,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    ok: int,
) -> None:
    # Arrange
    write_index(index_dir)

    # Act
    response = await call(client, method, suffix, body, legacy())

    # Assert
    assert response.status_code == ok


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "suffix", "body", "ok"), ROUTES, ids=ROUTE_IDS)
async def test_anonymous_and_wrong_key_are_refused(
    client: AsyncClient,
    launcher: FakeLauncher,
    index_dir: Path,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    ok: int,
) -> None:
    # Arrange
    write_index(index_dir)

    # Act
    anonymous = await call(client, method, suffix, body)
    wrong_key = await call(client, method, suffix, body, {"X-Admin-Api-Key": "nope"})

    # Assert
    assert anonymous.status_code == 401
    assert wrong_key.status_code == 401
    assert launcher.calls == 0


# ── GET /index ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_with_nothing_built_is_empty(client: AsyncClient) -> None:
    # Arrange
    # (an empty index root)

    # Act
    response = await call(client, "GET", "/index", None, legacy())

    # Assert
    assert response.status_code == 200
    assert response.json() == {
        "active_version": None,
        "manifest": None,
        "versions": [],
        "configured_embed_model": "bge-large",
    }


@pytest.mark.asyncio
async def test_index_reports_the_active_manifest_and_versions(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=False)
    write_index(index_dir, VERSION_B, active=True)

    # Act
    body = (await call(client, "GET", "/index", None, legacy())).json()

    # Assert
    assert body["active_version"] == VERSION_B
    assert body["versions"] == [VERSION_A, VERSION_B]
    assert set(body["manifest"]) == {
        "version",
        "created_at",
        "embed_model",
        "embed_digest",
        "dim",
        "chunk_count",
        "note_count",
        "exclusions",
        "unresolved_links",
        "warnings",
        "build_seconds",
        "cleaner_version",
        "chunker_version",
    }
    assert body["manifest"]["version"] == VERSION_B
    assert body["manifest"]["chunk_count"] == 1
    assert body["manifest"]["exclusions"] == {"redirect": 2, "planned": 1}
    assert body["manifest"]["embed_model"] == "nomic-embed-text"


@pytest.mark.asyncio
async def test_index_never_returns_note_text_or_paths(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    write_index(
        index_dir,
        note_hash_key=f"{CANARY}/note.md",
        error_code=CANARY,
        chunk_text=f"A synthetic passage with {CANARY} inside.",
    )

    # Act
    response = await call(client, "GET", "/index", None, legacy())

    # Assert
    assert response.status_code == 200
    assert CANARY not in response.text
    assert "note_hashes" not in response.json()["manifest"]


@pytest.mark.asyncio
async def test_index_follows_the_pointer_after_a_rollback(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)
    write_index(index_dir, VERSION_B, active=False)
    first = (await call(client, "GET", "/index", None, legacy())).json()

    # Act
    index_store.activate(index_dir, VERSION_B)
    second = (await call(client, "GET", "/index", None, legacy())).json()

    # Assert
    assert first["active_version"] == VERSION_A
    assert second["active_version"] == VERSION_B


@pytest.mark.asyncio
async def test_index_with_an_unusable_active_pointer_still_lists_versions(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=False)
    (index_dir / "ACTIVE").write_text("20260709T100000Z-missing0\n")

    # Act
    response = await call(client, "GET", "/index", None, legacy())

    # Assert
    assert response.status_code == 200
    assert response.json()["active_version"] is None
    assert response.json()["manifest"] is None
    assert response.json()["versions"] == [VERSION_A]


# ── POST /reindex ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reindex_starts_one_build_and_reports_running(
    client: AsyncClient, launcher: FakeLauncher, index_dir: Path
) -> None:
    # Arrange
    # (nothing running)

    # Act
    response = await call(client, "POST", "/reindex", None, legacy())

    # Assert
    body = response.json()
    assert response.status_code == 202
    assert body["state"] == "running"
    assert datetime.fromisoformat(body["started_at"])
    assert launcher.calls == 1
    written = build_status.read(index_dir)
    assert written.state is BuildState.running
    assert written.pid == launcher.pid
    assert written.started_at == body["started_at"]


@pytest.mark.asyncio
async def test_second_reindex_while_running_is_409_and_launches_nothing(
    client: AsyncClient, launcher: FakeLauncher
) -> None:
    # Arrange
    first = await call(client, "POST", "/reindex", None, legacy())

    # Act
    second = await call(client, "POST", "/reindex", None, legacy())

    # Assert
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json() == {"detail": "already_running"}
    assert launcher.calls == 1


@pytest.mark.asyncio
async def test_reindex_is_refused_while_a_live_builder_holds_the_lock(
    client: AsyncClient, launcher: FakeLauncher, index_dir: Path
) -> None:
    # Arrange
    (index_dir / build_status.LOCK_FILE).write_text(str(os.getpid()))

    # Act
    response = await call(client, "POST", "/reindex", None, legacy())

    # Assert
    assert response.status_code == 409
    assert launcher.calls == 0
    assert build_status.lock_owner(index_dir) == os.getpid()


@pytest.mark.asyncio
async def test_reindex_ignores_a_dead_builders_status_and_lock(
    client: AsyncClient, launcher: FakeLauncher, index_dir: Path
) -> None:
    # Arrange
    gone = dead_pid()
    build_status.write(
        index_dir, BuildStatus(state=BuildState.running, pid=gone, started_at="x")
    )
    (index_dir / build_status.LOCK_FILE).write_text(str(gone))

    # Act
    response = await call(client, "POST", "/reindex", None, legacy())

    # Assert
    assert response.status_code == 202
    assert launcher.calls == 1


@pytest.mark.asyncio
async def test_reindex_probe_leaves_no_lock_behind(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    # (nothing running, so the check takes and must release the lock)

    # Act
    await call(client, "POST", "/reindex", None, legacy())

    # Assert
    assert not (index_dir / build_status.LOCK_FILE).exists()


@pytest.mark.asyncio
async def test_reindex_launch_failure_is_500_and_leaves_no_running_status(
    client: AsyncClient, launcher: FakeLauncher, index_dir: Path
) -> None:
    # Arrange
    launcher.error = OSError("cannot spawn")

    # Act
    response = await call(client, "POST", "/reindex", None, legacy())

    # Assert
    assert response.status_code == 500
    assert response.json() == {"detail": "builder_launch_failed"}
    assert build_status.read(index_dir).state is BuildState.idle


@pytest.mark.asyncio
async def test_reindex_by_a_named_admin_is_audited_without_content(
    client: AsyncClient,
    make_staff: StaffFactory,
    db_session: AsyncSession,
) -> None:
    # Arrange
    admin, headers = await make_staff(UserRole.admin)

    # Act
    response = await call(client, "POST", "/reindex", None, headers)

    # Assert
    rows = await audit_rows(db_session)
    assert response.status_code == 202
    assert len(rows) == 1
    assert rows[0].action == "priest.reindex"
    assert rows[0].actor_user_id == admin.id
    assert rows[0].detail is None
    assert rows[0].justification is None
    assert rows[0].target_confession_id is None


@pytest.mark.asyncio
async def test_reindex_with_the_legacy_key_writes_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Arrange
    # (the shared key names nobody, so there is no actor to record)

    # Act
    response = await call(client, "POST", "/reindex", None, legacy())

    # Assert
    assert response.status_code == 202
    assert await audit_rows(db_session) == []


@pytest.mark.asyncio
async def test_refused_and_failed_reindex_are_not_audited(
    client: AsyncClient,
    make_staff: StaffFactory,
    launcher: FakeLauncher,
    db_session: AsyncSession,
) -> None:
    # Arrange
    _, headers = await make_staff(UserRole.admin)
    launcher.error = OSError("cannot spawn")
    failed = await call(client, "POST", "/reindex", None, headers)
    rows_after_failure = len(await audit_rows(db_session))
    launcher.error = None
    started = await call(client, "POST", "/reindex", None, headers)

    # Act
    conflict = await call(client, "POST", "/reindex", None, headers)

    # Assert
    assert failed.status_code == 500
    assert rows_after_failure == 0
    assert started.status_code == 202
    assert conflict.status_code == 409
    assert len(await audit_rows(db_session)) == 1


# ── The default launcher ─────────────────────────────────────────────────


class _FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.reaped = threading.Event()

    def wait(self) -> int:
        self.reaped.set()
        return 0


def test_default_launcher_starts_a_detached_builder_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    seen: dict[str, Any] = {}
    process = _FakeProcess()

    def fake_popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return process

    monkeypatch.setattr(priest_admin.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PRIEST_TEST_MARKER", "marker")

    # Act
    pid = priest_admin.spawn_builder()

    # Assert
    kwargs = seen["kwargs"]
    assert pid == 4242
    assert seen["argv"] == [sys.executable, "-m", "app.priest.index_builder"]
    assert not kwargs.get("shell")
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["PRIEST_TEST_MARKER"] == "marker"
    assert kwargs["env"] is not os.environ
    assert (Path(kwargs["cwd"]) / "app" / "priest").is_dir()
    assert process.reaped.wait(timeout=2)


# ── GET /reindex/status ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_with_no_build_is_idle(client: AsyncClient) -> None:
    # Arrange
    # (no status file)

    # Act
    response = await call(client, "GET", "/reindex/status", None, legacy())

    # Assert
    assert response.status_code == 200
    assert response.json() == {
        "state": "idle",
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "notes_total": 0,
        "notes_indexed": 0,
        "chunks": 0,
        "exclusions": {},
        "error_code": None,
    }


@pytest.mark.asyncio
async def test_status_passes_through_the_builders_counts(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    build_status.write(
        index_dir,
        BuildStatus(
            state=BuildState.succeeded,
            started_at="2026-09-30T10:00:00Z",
            finished_at="2026-09-30T10:05:00Z",
            pid=123,
            notes_total=10,
            notes_indexed=8,
            chunks=40,
            exclusions={"redirect": 2},
        ),
    )

    # Act
    body = (await call(client, "GET", "/reindex/status", None, legacy())).json()

    # Assert
    assert body["state"] == "succeeded"
    assert body["notes_total"] == 10
    assert body["notes_indexed"] == 8
    assert body["chunks"] == 40
    assert body["exclusions"] == {"redirect": 2}
    assert body["finished_at"] == "2026-09-30T10:05:00Z"


@pytest.mark.asyncio
async def test_status_of_a_dead_builder_reads_as_failed(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    build_status.write(index_dir, BuildStatus(state=BuildState.running, pid=dead_pid()))

    # Act
    body = (await call(client, "GET", "/reindex/status", None, legacy())).json()

    # Assert
    assert body["state"] == "failed"
    assert body["error_code"] == "builder_died"


# ── POST /activate ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_switches_the_pointer_and_audits_the_version_only(
    client: AsyncClient,
    make_staff: StaffFactory,
    index_dir: Path,
    db_session: AsyncSession,
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)
    write_index(index_dir, VERSION_B, active=False)
    admin, headers = await make_staff(UserRole.admin)

    # Act
    response = await call(client, "POST", "/activate", {"version": VERSION_B}, headers)

    # Assert
    rows = await audit_rows(db_session)
    assert response.status_code == 200
    assert response.json() == {"active_version": VERSION_B}
    assert (index_dir / "ACTIVE").read_text().strip() == VERSION_B
    assert len(rows) == 1
    assert rows[0].action == "priest.activate"
    assert rows[0].actor_user_id == admin.id
    assert rows[0].detail == VERSION_B
    assert rows[0].justification is None


@pytest.mark.asyncio
async def test_activate_with_the_legacy_key_works_without_an_audit_row(
    client: AsyncClient, index_dir: Path, db_session: AsyncSession
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)
    write_index(index_dir, VERSION_B, active=False)

    # Act
    response = await call(client, "POST", "/activate", {"version": VERSION_B}, legacy())

    # Assert
    assert response.status_code == 200
    assert (index_dir / "ACTIVE").read_text().strip() == VERSION_B
    assert await audit_rows(db_session) == []


@pytest.mark.asyncio
async def test_activate_unknown_version_is_404_and_changes_nothing(
    client: AsyncClient,
    make_staff: StaffFactory,
    index_dir: Path,
    db_session: AsyncSession,
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await call(
        client, "POST", "/activate", {"version": "20260709T100000Z-cccccccc"}, headers
    )

    # Assert
    assert response.status_code == 404
    assert (index_dir / "ACTIVE").read_text().strip() == VERSION_A
    assert await audit_rows(db_session) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version",
    [
        "../secret",
        "..",
        ".",
        "a/b",
        "a\\b",
        "/etc/passwd",
        "%2e%2e%2fsecret",
        ".hidden",
        "-leading-dash",
        "with space",
        "new\nline",
        f"{VERSION_A}\n",
        "",
        "x" * 65,
    ],
)
async def test_activate_rejects_names_that_are_not_version_names(
    client: AsyncClient,
    make_staff: StaffFactory,
    index_dir: Path,
    db_session: AsyncSession,
    version: str,
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)
    write_index(index_dir.parent / "secret", VERSION_B, active=False)
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await call(client, "POST", "/activate", {"version": version}, headers)

    # Assert
    assert response.status_code == 422
    assert (index_dir / "ACTIVE").read_text().strip() == VERSION_A
    assert await audit_rows(db_session) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{}, {"version": 5}, {"version": VERSION_A, "extra": 1}, None]
)
async def test_activate_rejects_a_malformed_body(
    client: AsyncClient, index_dir: Path, body: dict[str, Any] | None
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)

    # Act
    response = await call(client, "POST", "/activate", body, legacy())

    # Assert
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activate_a_version_that_will_not_load_is_409_and_changes_nothing(
    client: AsyncClient,
    make_staff: StaffFactory,
    index_dir: Path,
    db_session: AsyncSession,
) -> None:
    # Arrange
    write_index(index_dir, VERSION_A, active=True)
    (index_dir / "versions" / VERSION_B).mkdir()
    _, headers = await make_staff(UserRole.admin)

    # Act
    response = await call(client, "POST", "/activate", {"version": VERSION_B}, headers)

    # Assert
    assert response.status_code == 409
    assert response.json() == {"detail": "version_not_loadable"}
    assert (index_dir / "ACTIVE").read_text().strip() == VERSION_A
    assert await audit_rows(db_session) == []


# ── GET /usage ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_lists_every_outcome_and_stage_even_at_zero(
    client: AsyncClient,
) -> None:
    # Arrange
    # (nothing recorded)

    # Act
    body = (await call(client, "GET", "/usage", None, legacy())).json()

    # Assert
    assert [o["kind"] for o in body["outcomes"]] == list(metrics.OUTCOMES)
    assert all(o == {**o, "count": None, "suppressed": True} for o in body["outcomes"])
    assert [row["stage"] for row in body["latency"]] == list(metrics.STAGES)
    assert all(row["p50"] is None and row["p95"] is None for row in body["latency"])
    assert datetime.fromisoformat(body["since"])


@pytest.mark.asyncio
async def test_usage_suppresses_counts_below_the_cohort_and_keeps_the_rest(
    client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ANALYTICS_MIN_COHORT", 5)
    for _ in range(5):
        metrics.record_outcome("answer")
    for _ in range(4):
        metrics.record_outcome("not_covered")
    metrics.record_outcome("crisis")

    # Act
    body = (await call(client, "GET", "/usage", None, legacy())).json()

    # Assert
    by_kind = {o["kind"]: o for o in body["outcomes"]}
    assert body["min_cohort"] == 5
    assert by_kind["answer"] == {"kind": "answer", "count": 5, "suppressed": False}
    assert by_kind["not_covered"]["count"] is None
    assert by_kind["not_covered"]["suppressed"] is True
    assert by_kind["crisis"]["count"] is None
    assert by_kind["crisis"]["suppressed"] is True
    assert by_kind["deferral"]["suppressed"] is True


@pytest.mark.asyncio
async def test_usage_counts_up_to_the_cohort_boundary_are_suppressed_exactly(
    client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ANALYTICS_MIN_COHORT", 3)
    for _ in range(2):
        metrics.record_outcome("error")
    for _ in range(3):
        metrics.record_outcome("busy")

    # Act
    body = (await call(client, "GET", "/usage", None, legacy())).json()

    # Assert
    by_kind = {o["kind"]: o for o in body["outcomes"]}
    assert by_kind["error"]["suppressed"] is True
    assert by_kind["busy"]["suppressed"] is False
    assert by_kind["busy"]["count"] == 3


@pytest.mark.asyncio
async def test_usage_latency_is_not_suppressed(client: AsyncClient) -> None:
    # Arrange
    metrics.record_latency("total", 0.25)

    # Act
    body = (await call(client, "GET", "/usage", None, legacy())).json()

    # Assert
    total = next(row for row in body["latency"] if row["stage"] == "total")
    assert total == {"stage": "total", "p50": 0.25, "p95": 0.25}


@pytest.mark.asyncio
async def test_usage_reports_an_unknown_outcome_under_other_and_only_then(
    client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ANALYTICS_MIN_COHORT", 2)
    before = (await call(client, "GET", "/usage", None, legacy())).json()
    for _ in range(2):
        metrics.record_outcome("something-new")

    # Act
    after = (await call(client, "GET", "/usage", None, legacy())).json()

    # Assert
    assert "other" not in [o["kind"] for o in before["outcomes"]]
    other = next(o for o in after["outcomes"] if o["kind"] == "other")
    assert other == {"kind": "other", "count": 2, "suppressed": False}


# ── GET /health ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_with_nothing_configured_probes_only_the_embedder(
    client: AsyncClient, probes: Probes
) -> None:
    # Arrange
    # (no primary, no fallback model: the embedder still has a default address)

    # Act
    body = (await call(client, "GET", "/health", None, legacy())).json()

    # Assert
    unconfigured = {
        "configured": False,
        "reachable": None,
        "kind": None,
        "host": None,
    }
    assert body["primary"] == unconfigured
    assert body["fallback"] == unconfigured
    assert body["embedder"] == {"reachable": True, "model": "bge-large"}
    assert [str(r.url) for r in probes.requests] == ["http://localhost:11434/api/tags"]


@pytest.mark.asyncio
async def test_health_probes_each_server_without_a_body_and_keeps_the_key_home(
    client: AsyncClient, probes: Probes, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "https://vllm.internal:8000")
    set_setting("PRIEST_LLM_MODEL", "served-model")
    set_setting("PRIEST_LLM_API_KEY", PRIMARY_KEY)
    set_setting("PRIEST_FALLBACK_MODEL", "qwen-small")

    # Act
    response = await call(client, "GET", "/health", None, legacy())

    # Assert
    body = response.json()
    by_url = {str(r.url): r for r in probes.requests}
    assert set(by_url) == {
        "https://vllm.internal:8000/v1/models",
        "http://localhost:11434/v1/models",
        "http://localhost:11434/api/tags",
    }
    assert all(r.method == "GET" and r.content == b"" for r in probes.requests)
    primary = by_url["https://vllm.internal:8000/v1/models"]
    assert primary.headers["authorization"] == f"Bearer {PRIMARY_KEY}"
    for url in ("http://localhost:11434/v1/models", "http://localhost:11434/api/tags"):
        assert "authorization" not in by_url[url].headers
    assert not any(PRIMARY_KEY in str(r.url) for r in probes.requests)
    assert PRIMARY_KEY not in response.text
    assert body["primary"] == {
        "configured": True,
        "reachable": True,
        "kind": "vllm",
        "host": "vllm.internal",
    }
    assert body["fallback"] == {
        "configured": True,
        "reachable": True,
        "kind": "ollama",
        "host": "localhost",
    }


@pytest.mark.asyncio
async def test_health_never_sends_a_key_to_an_ollama_endpoint(
    client: AsyncClient, probes: Probes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    leaky = ChatEndpoint(
        base_url="http://ollama.internal:11434",
        model="m",
        api_key=PRIMARY_KEY,
        timeout_seconds=5,
        host="ollama.internal",
        kind="ollama",
    )
    monkeypatch.setattr(priest_admin.chat_endpoint, "resolve_fallback", lambda: leaky)

    # Act
    await call(client, "GET", "/health", None, legacy())

    # Assert
    probed = [r for r in probes.requests if r.url.host == "ollama.internal"]
    assert len(probed) == 1
    assert "authorization" not in probed[0].headers


@pytest.mark.asyncio
async def test_health_sends_no_key_when_none_is_configured(
    client: AsyncClient, probes: Probes, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "https://vllm.internal:8000")
    set_setting("PRIEST_LLM_MODEL", "served-model")

    # Act
    await call(client, "GET", "/health", None, legacy())

    # Assert
    assert all("authorization" not in r.headers for r in probes.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["connect_error", "server_error", "unauthorised", "redirect"],
)
async def test_health_reports_unreachable_for_every_kind_of_failure(
    client: AsyncClient, probes: Probes, set_setting: SettingPatcher, outcome: str
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "https://vllm.internal:8000")
    set_setting("PRIEST_LLM_MODEL", "served-model")
    target = "vllm.internal/v1/models"
    if outcome == "connect_error":
        probes.down.add(target)
    else:
        probes.status_for[target] = {
            "server_error": 503,
            "unauthorised": 401,
            "redirect": 302,
        }[outcome]

    # Act
    body = (await call(client, "GET", "/health", None, legacy())).json()

    # Assert
    assert body["primary"]["configured"] is True
    assert body["primary"]["reachable"] is False
    assert body["embedder"]["reachable"] is True
    assert sum(r.url.host == "elsewhere.invalid" for r in probes.requests) == 0


@pytest.mark.asyncio
async def test_health_reports_an_embedder_that_is_down(
    client: AsyncClient, probes: Probes
) -> None:
    # Arrange
    probes.down.add("localhost/api/tags")

    # Act
    body = (await call(client, "GET", "/health", None, legacy())).json()

    # Assert
    assert body["embedder"] == {"reachable": False, "model": "bge-large"}


@pytest.mark.asyncio
async def test_health_never_echoes_what_a_server_answers(
    client: AsyncClient, probes: Probes, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "https://vllm.internal:8000")
    set_setting("PRIEST_LLM_MODEL", "served-model")
    probes.body = b'{"data": [{"id": "SERVER-REPLY-CANARY"}]}'

    # Act
    response = await call(client, "GET", "/health", None, legacy())

    # Assert
    assert "SERVER-REPLY-CANARY" not in response.text


@pytest.mark.asyncio
async def test_health_refuses_to_probe_a_hosted_provider(
    client: AsyncClient, probes: Probes, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("PRIEST_LLM_BASE_URL", "https://api.openai.com")
    set_setting("PRIEST_LLM_MODEL", "served-model")
    set_setting("PRIEST_LLM_API_KEY", PRIMARY_KEY)

    # Act
    response = await call(client, "GET", "/health", None, legacy())

    # Assert
    assert response.json()["primary"] == {
        "configured": False,
        "reachable": None,
        "kind": None,
        "host": None,
    }
    assert "openai" not in response.text
    assert all(r.url.host != "api.openai.com" for r in probes.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    ["https://api.anthropic.com", "ftp://localhost", "not a url", "http://u:p@h"],
)
async def test_health_does_not_probe_an_unusable_embedder_address(
    client: AsyncClient,
    probes: Probes,
    set_setting: SettingPatcher,
    address: str,
) -> None:
    # Arrange
    set_setting("OLLAMA_BASE_URL", address)

    # Act
    body = (await call(client, "GET", "/health", None, legacy())).json()

    # Assert
    assert body["embedder"] == {"reachable": None, "model": "bge-large"}
    assert probes.requests == []


@pytest.mark.asyncio
async def test_the_real_probe_client_is_short_and_follows_nothing() -> None:
    # Arrange
    # (the client the route builds when nothing is overridden)

    # Act
    async with priest_admin.build_probe_client() as probe_client:
        # Assert
        assert probe_client.timeout == httpx.Timeout(3.0)
        assert probe_client.follow_redirects is False
        assert probe_client.trust_env is False
        assert probe_client.headers.get("authorization") is None


# ── GET /report ──────────────────────────────────────────────────────────


def reports(index_dir: Path) -> Path:
    path = index_dir / "reports"
    path.mkdir(exist_ok=True)
    return path


async def report_body(client: AsyncClient) -> dict[str, Any]:
    response = await call(client, "GET", "/report", None, legacy())
    assert response.status_code == 200
    return response.json()


UNAVAILABLE = {"available": False, "summary": None}


@pytest.mark.asyncio
async def test_report_without_a_reports_directory_is_unavailable(
    client: AsyncClient,
) -> None:
    # Arrange
    # (no reports directory)

    # Act
    body = await report_body(client)

    # Assert
    assert body == UNAVAILABLE


@pytest.mark.asyncio
async def test_report_in_an_empty_directory_is_unavailable(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    (reports(index_dir) / "notes.txt").write_text("{}")

    # Act
    body = await report_body(client)

    # Assert
    assert body == UNAVAILABLE


@pytest.mark.asyncio
async def test_report_returns_the_newest_json_summary(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    older = reports(index_dir) / "eval-a.json"
    newer = reports(index_dir) / "eval-b.json"
    older.write_text(json.dumps({"run": "old"}))
    newer.write_text(json.dumps({"run": "new", "score": 0.9}))
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    # Act
    body = await report_body(client)

    # Assert
    assert body == {"available": True, "summary": {"run": "new", "score": 0.9}}


@pytest.mark.asyncio
async def test_report_ignores_files_that_are_not_json(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    real = reports(index_dir) / "eval.json"
    real.write_text(json.dumps({"ok": True}))
    decoy = reports(index_dir) / "newer.txt"
    decoy.write_text(json.dumps({"ok": False}))
    os.utime(real, (1_000_000, 1_000_000))
    os.utime(decoy, (2_000_000, 2_000_000))

    # Act
    body = await report_body(client)

    # Assert
    assert body["summary"] == {"ok": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b"",
        b"[1, 2, 3]",
        b"42",
        b'"a string"',
        b"null",
        b'{"score": NaN}',
        b'{"score": Infinity}',
        b"\xff\xfe\xfa",
        b"[" * 20000,
    ],
    ids=[
        "malformed",
        "empty",
        "list",
        "number",
        "string",
        "null",
        "nan",
        "infinity",
        "bad-utf8",
        "deeply-nested",
    ],
)
async def test_report_that_is_not_a_json_object_is_unavailable(
    client: AsyncClient, index_dir: Path, content: bytes
) -> None:
    # Arrange
    (reports(index_dir) / "eval.json").write_bytes(content)

    # Act
    body = await report_body(client)

    # Assert
    assert body == UNAVAILABLE


@pytest.mark.asyncio
async def test_report_is_size_limited_to_64_kb_inclusive(
    client: AsyncClient, index_dir: Path
) -> None:
    # Arrange
    prefix = b'{"pad": "'
    suffix = b'"}'
    room = REPORT_LIMIT - len(prefix) - len(suffix)
    target = reports(index_dir) / "eval.json"
    target.write_bytes(prefix + b"x" * room + suffix)
    at_limit = await report_body(client)
    target.write_bytes(prefix + b"x" * (room + 1) + suffix)

    # Act
    over_limit = await report_body(client)

    # Assert
    assert at_limit["available"] is True
    assert over_limit == UNAVAILABLE


@pytest.mark.asyncio
async def test_report_skips_directories_and_symlinks_and_stays_inside(
    client: AsyncClient, index_dir: Path, tmp_path: Path
) -> None:
    # Arrange
    real = reports(index_dir) / "real.json"
    real.write_text(json.dumps({"from": "real"}))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"from": "outside"}))
    (reports(index_dir) / "link.json").symlink_to(outside)
    (reports(index_dir) / "dir.json").mkdir()
    os.utime(real, (1_000_000, 1_000_000))
    os.utime(outside, (3_000_000, 3_000_000))
    os.utime(
        reports(index_dir) / "link.json", (3_000_000, 3_000_000), follow_symlinks=False
    )

    # Act
    body = await report_body(client)

    # Assert
    assert body["summary"] == {"from": "real"}


@pytest.mark.asyncio
async def test_report_does_not_read_through_a_symlinked_reports_directory(
    client: AsyncClient, index_dir: Path, tmp_path: Path
) -> None:
    # Arrange
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "eval.json").write_text(json.dumps({"from": "elsewhere"}))
    (index_dir / "reports").symlink_to(elsewhere, target_is_directory=True)

    # Act
    body = await report_body(client)

    # Assert
    assert body == UNAVAILABLE


def test_report_limit_constant_matches_the_contract() -> None:
    # Arrange
    # (the contract says 64 KB)

    # Act
    limit = priest_admin.MAX_REPORT_BYTES

    # Assert
    assert limit == REPORT_LIMIT
    assert math.isfinite(limit)


# ── Wiring ───────────────────────────────────────────────────────────────


def test_router_has_the_contracted_prefix_and_routes() -> None:
    # Arrange
    expected = {
        ("GET", "/admin/priest/index"),
        ("POST", "/admin/priest/reindex"),
        ("GET", "/admin/priest/reindex/status"),
        ("POST", "/admin/priest/activate"),
        ("GET", "/admin/priest/health"),
        ("GET", "/admin/priest/usage"),
        ("GET", "/admin/priest/report"),
    }

    # Act
    actual = {
        (method, route.path)  # type: ignore[attr-defined]
        for route in router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }

    # Assert
    assert actual == expected
    assert router.prefix == "/admin/priest"


def test_new_audit_actions_have_the_contracted_values() -> None:
    # Arrange
    # (stored as plain strings, so no migration)

    # Act
    values = {AuditAction.priest_reindex.value, AuditAction.priest_activate.value}

    # Assert
    assert values == {"priest.reindex", "priest.activate"}
