"""The role matrix: every route, every role, including the refusals.

Rather than hand-listing the endpoints worth checking, this enumerates every
route the app defines. A new route must be added to exactly one of the tables
below, which forces an explicit decision about who may call it: a route
added without one fails ``test_every_route_is_classified``, and a staff route
is then proven to refuse an anonymous caller and every role not allowed.
"""

from __future__ import annotations

import uuid

import pytest
from app.main import app
from app.models.user import UserRole
from httpx import AsyncClient

from tests.conftest import SettingPatcher, StaffFactory
from tests.route_listing import flattened_routes

ADMIN = {UserRole.admin}
HR = {UserRole.hr, UserRole.admin}
QUEUE = {UserRole.moderator, UserRole.hr, UserRole.admin}
ANY_STAFF = {UserRole.moderator, UserRole.hr, UserRole.admin}

# Staff routes: who may call each. ``probe`` is False where an authorised call
# would have a side effect this test must not trigger (a build, a network call).
StaffRoute = tuple[str, str]
STAFF_ROUTES: dict[StaffRoute, tuple[set[UserRole], bool]] = {
    ("GET", "/api/v1/hr/confessions"): (HR, True),
    ("GET", "/api/v1/hr/confessions/{confession_id}"): (HR, True),
    ("POST", "/api/v1/hr/confessions/{confession_id}/raw"): (HR, True),
    ("PUT", "/api/v1/hr/confessions/{confession_id}/reply"): (HR, True),
    ("GET", "/api/v1/hr/insights"): (HR, True),
    ("GET", "/api/v1/hr/themes"): (HR, False),
    ("GET", "/api/v1/privacy/overview"): (HR, True),
    ("GET", "/api/v1/delivery/overview"): (HR, True),
    ("POST", "/api/v1/delivery/{confession_id}/resend"): (HR, True),
    ("GET", "/api/v1/departments/directory"): (HR, True),
    ("POST", "/api/v1/departments/directory"): (ADMIN, True),
    ("PUT", "/api/v1/departments/directory/{name}"): (ADMIN, True),
    ("DELETE", "/api/v1/departments/directory/{name}"): (ADMIN, True),
    ("GET", "/api/v1/audit"): (ADMIN, True),
    ("GET", "/api/v1/audit/actions"): (ADMIN, True),
    ("GET", "/api/v1/moderation/queue"): (QUEUE, True),
    ("POST", "/api/v1/moderation/{confession_id}/acknowledge"): (QUEUE, True),
    ("POST", "/api/v1/moderation/{confession_id}/approve"): (QUEUE, True),
    ("POST", "/api/v1/moderation/{confession_id}/reject"): (QUEUE, True),
    # Priest mode operator surface (13.22): admin only. Reindex starts a build and
    # health makes network calls, so an authorised call is not probed.
    ("GET", "/api/v1/admin/priest/index"): (ADMIN, True),
    ("POST", "/api/v1/admin/priest/reindex"): (ADMIN, False),
    ("GET", "/api/v1/admin/priest/reindex/status"): (ADMIN, True),
    ("POST", "/api/v1/admin/priest/activate"): (ADMIN, True),
    ("GET", "/api/v1/admin/priest/health"): (ADMIN, False),
    ("GET", "/api/v1/admin/priest/usage"): (ADMIN, True),
    ("GET", "/api/v1/admin/priest/report"): (ADMIN, True),
    ("GET", "/api/v1/auth/me"): (ANY_STAFF, True),
    ("POST", "/api/v1/auth/logout"): (ANY_STAFF, True),
    ("POST", "/api/v1/admin/build-apk"): (ADMIN, False),
    ("GET", "/api/v1/admin/build-apk/status"): (ADMIN, True),
    ("GET", "/api/v1/admin/config"): (ADMIN, True),
    ("PUT", "/api/v1/admin/config"): (ADMIN, False),
    ("DELETE", "/api/v1/admin/config/{key}"): (ADMIN, False),
    ("GET", "/api/v1/admin/livekit"): (ADMIN, False),
    ("GET", "/api/v1/admin/ngrok"): (ADMIN, False),
}

# Moderation accepts a staff session *or* the bot's shared key, so a caller with
# neither is "refused" (403) rather than "unauthenticated" (401) — decided in 11.8.
REFUSES_WITH_403: set[StaffRoute] = {
    route for route in STAFF_ROUTES if route[1].startswith("/api/v1/moderation/")
}

# Routes a shared secret guards; they must refuse a caller with no key.
KEYED_ROUTES: set[StaffRoute] = {
    ("GET", "/api/v1/delivery/queue"),
    ("POST", "/api/v1/delivery/{confession_id}/delivered"),
    ("GET", "/metrics"),
}

# Routes open by design: the confessor's own (scoped by their device hash),
# sign-in, and health. None returns staff data or staff-only content.
OPEN_ROUTES: set[StaffRoute] = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/health"),
    ("GET", "/health"),
    ("POST", "/api/v1/confessions"),
    ("GET", "/api/v1/confessions"),
    ("POST", "/api/v1/confessions/preview"),
    ("GET", "/api/v1/confessions/{confession_id}"),
    ("DELETE", "/api/v1/confessions/{confession_id}"),
    ("POST", "/api/v1/confessions/{confession_id}/forward"),
    ("GET", "/api/v1/departments"),
    ("POST", "/api/v1/stt"),
    ("POST", "/api/v1/tts"),
    ("POST", "/api/v1/voice/mask"),
    # The Guide: scoped by the device header like the confessor's own routes, and it
    # returns no staff data. /ask is gated by the kill switch and a per-device limit.
    ("GET", "/api/v1/priest/status"),
    ("POST", "/api/v1/priest/ask"),
}


def _defined_routes() -> set[StaffRoute]:
    return {
        (method, route.path)
        for route in flattened_routes(app)
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }


def _concrete(path: str) -> str:
    """Fill path parameters with a value that matches nothing real."""
    return (
        path.replace("{confession_id}", str(uuid.uuid4()))
        .replace("{name}", "no-such-department")
        .replace("{key}", "NO_SUCH_KEY")
    )


def _label(route: StaffRoute) -> str:
    return f"{route[0]} {route[1]}"


def test_every_route_is_classified() -> None:
    # Arrange
    classified = set(STAFF_ROUTES) | KEYED_ROUTES | OPEN_ROUTES

    # Act
    defined = _defined_routes()

    # Assert — a new route must be added to a table above, with a decision
    assert defined - classified == set(), "route(s) with no access decision"
    assert classified - defined == set(), (
        "table entry for a route that no longer exists"
    )


def test_no_route_is_in_two_tables() -> None:
    # Act / Assert
    assert not (set(STAFF_ROUTES) & KEYED_ROUTES)
    assert not (set(STAFF_ROUTES) & OPEN_ROUTES)
    assert not (KEYED_ROUTES & OPEN_ROUTES)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(STAFF_ROUTES), ids=_label)
async def test_a_staff_route_refuses_an_anonymous_caller(
    route: StaffRoute, api_client: AsyncClient
) -> None:
    # Arrange
    method, path = route

    # Act
    response = await api_client.request(method, _concrete(path), json={})

    # Assert
    assert response.status_code == (403 if route in REFUSES_WITH_403 else 401)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(UserRole), ids=lambda role: role.value)
@pytest.mark.parametrize("route", sorted(STAFF_ROUTES), ids=_label)
async def test_a_staff_route_admits_only_the_roles_it_names(
    route: StaffRoute,
    role: UserRole,
    api_client: AsyncClient,
    make_staff: StaffFactory,
) -> None:
    # Arrange
    allowed, probe_allowed = STAFF_ROUTES[route]
    if role in allowed and not probe_allowed:
        pytest.skip("an authorised call has a side effect this test must not trigger")
    _, headers = await make_staff(role)
    method, path = route

    # Act
    response = await api_client.request(
        method, _concrete(path), json={}, headers=headers
    )

    # Assert
    if role in allowed:
        assert response.status_code not in (401, 403)
        assert response.status_code < 500
    else:
        assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    sorted(r for r in STAFF_ROUTES if not r[1].startswith("/api/v1/admin/")),
    ids=_label,
)
async def test_the_legacy_admin_key_opens_only_the_admin_routes(
    route: StaffRoute, api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — the shared key has no named actor, so it can never be audited
    set_setting("ADMIN_API_KEY", "legacy-key-for-tests")
    method, path = route

    # Act
    response = await api_client.request(
        method,
        _concrete(path),
        json={},
        headers={"X-Admin-Api-Key": "legacy-key-for-tests"},
    )

    # Assert
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_the_legacy_admin_key_does_open_the_admin_config_read(
    api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange
    set_setting("ADMIN_API_KEY", "legacy-key-for-tests")

    # Act
    response = await api_client.get(
        "/api/v1/admin/config", headers={"X-Admin-Api-Key": "legacy-key-for-tests"}
    )

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(KEYED_ROUTES), ids=_label)
async def test_a_keyed_route_refuses_a_caller_with_no_key(
    route: StaffRoute, api_client: AsyncClient, set_setting: SettingPatcher
) -> None:
    # Arrange — even with a key configured, no header means no access
    set_setting("DELIVERY_API_KEY", "delivery-key-for-tests")
    set_setting("METRICS_API_KEY", "metrics-key-for-tests")
    method, path = route

    # Act
    response = await api_client.request(method, _concrete(path), json={})

    # Assert — 422 is FastAPI rejecting the request for its missing key header
    # before any handler runs; nothing is returned
    assert response.status_code in (401, 403, 422)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(KEYED_ROUTES), ids=_label)
async def test_a_keyed_route_fails_closed_when_no_key_is_configured(
    route: StaffRoute,
    api_client: AsyncClient,
    make_staff: StaffFactory,
    set_setting: SettingPatcher,
) -> None:
    # Arrange — an unset secret must deny everyone, including an admin session
    set_setting("DELIVERY_API_KEY", "")
    set_setting("METRICS_API_KEY", "")
    _, headers = await make_staff(UserRole.admin)
    method, path = route

    # Act
    response = await api_client.request(
        method, _concrete(path), json={}, headers=headers
    )

    # Assert
    assert response.status_code in (401, 403, 422)
