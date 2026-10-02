"""Tests for the device-scoped priest rate limiter.

The limiter holds only HMAC-keyed hit times: the raw device code must never be a key
or sit in its state, a refused question must not count, and windows slide with the
injected clock rather than resetting on a boundary.
"""

from __future__ import annotations

from app.exceptions import RateLimitError
from app.priest.rate_limiter import (
    FIXED_REPLY_ADDRESS_LIMITS,
    FIXED_REPLY_DEVICE_LIMITS,
    PriestRateLimiter,
    PriestRateLimitError,
)

from tests.conftest import SettingPatcher

DEVICE = "device-hash-0123456789abcdef"
MINUTE = 60.0
DAY = 86400.0


class FakeClock:
    """A settable clock so windows can be slid without sleeping."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _limiter(clock: FakeClock, max_keys: int = 50_000) -> PriestRateLimiter:
    return PriestRateLimiter(clock=clock, max_keys=max_keys)


def _limits(set_setting: SettingPatcher, per_minute: int, per_day: int) -> None:
    set_setting("PRIEST_RATE_LIMIT_PER_MINUTE", per_minute)
    set_setting("PRIEST_RATE_LIMIT_PER_DAY", per_day)


def _refusal(limiter: PriestRateLimiter, device: str = DEVICE) -> PriestRateLimitError:
    """Return the error the next hit raises; fail if it does not raise."""
    try:
        limiter.check_and_record(device)
    except PriestRateLimitError as exc:
        return exc
    raise AssertionError("expected the limiter to refuse")


def test_allows_hits_up_to_the_minute_limit_then_refuses(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    clock = FakeClock()
    limiter = _limiter(clock)

    # Act
    for _ in range(4):
        limiter.check_and_record(DEVICE)
    refusal = _refusal(limiter)

    # Assert
    assert isinstance(refusal.retry_after_seconds, int)
    assert refusal.retry_after_seconds == 60


def test_error_is_a_rate_limit_error_with_a_fixed_message(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _limits(set_setting, per_minute=1, per_day=40)
    limiter = _limiter(FakeClock())
    limiter.check_and_record(DEVICE)

    # Act
    refusal = _refusal(limiter)

    # Assert
    assert isinstance(refusal, RateLimitError)
    assert str(refusal) == "rate_limited"
    assert DEVICE not in str(refusal)


def test_minute_window_slides_with_the_clock(set_setting: SettingPatcher) -> None:
    # Arrange: hits at t=0, 10, 20, 30 fill a limit of 4
    _limits(set_setting, per_minute=4, per_day=40)
    clock = FakeClock(0.0)
    limiter = _limiter(clock)
    for at in (0.0, 10.0, 20.0, 30.0):
        clock.now = at
        limiter.check_and_record(DEVICE)

    # Act: the oldest hit leaves the window at t=60, not before
    clock.now = 40.0
    early = _refusal(limiter)
    clock.now = 59.9
    still_full = _refusal(limiter)
    clock.now = 60.0
    limiter.check_and_record(DEVICE)

    # Assert
    assert early.retry_after_seconds == 20
    assert still_full.retry_after_seconds == 1


def test_a_refused_question_is_not_counted(set_setting: SettingPatcher) -> None:
    # Arrange: limit 2, hits at t=0 and t=1
    _limits(set_setting, per_minute=2, per_day=40)
    clock = FakeClock(0.0)
    limiter = _limiter(clock)
    limiter.check_and_record(DEVICE)
    clock.now = 1.0
    limiter.check_and_record(DEVICE)

    # Act: three refusals, then the window slides past both real hits
    for at in (2.0, 3.0, 4.0):
        clock.now = at
        _refusal(limiter)
    clock.now = 61.5
    limiter.check_and_record(DEVICE)
    limiter.check_and_record(DEVICE)

    # Assert: had a refusal counted, the second call above would have been refused
    assert _refusal(limiter).retry_after_seconds >= 1


def test_day_limit_refuses_after_the_minute_window_has_cleared(
    set_setting: SettingPatcher,
) -> None:
    # Arrange: the minute limit is out of the way; the day limit is 3
    _limits(set_setting, per_minute=100, per_day=3)
    clock = FakeClock(0.0)
    limiter = _limiter(clock)
    for at in (0.0, 100.0, 200.0):
        clock.now = at
        limiter.check_and_record(DEVICE)

    # Act
    clock.now = 300.0
    refusal = _refusal(limiter)
    clock.now = DAY
    limiter.check_and_record(DEVICE)

    # Assert: the first hit (t=0) frees a slot at t=86400
    assert refusal.retry_after_seconds == int(DAY - 300.0)


def test_retry_after_is_the_longer_wait_when_both_windows_are_full(
    set_setting: SettingPatcher,
) -> None:
    # Arrange: per-minute 2, per-day 2, both hit at t=0 and t=1
    _limits(set_setting, per_minute=2, per_day=2)
    clock = FakeClock(0.0)
    limiter = _limiter(clock)
    limiter.check_and_record(DEVICE)
    clock.now = 1.0
    limiter.check_and_record(DEVICE)

    # Act
    clock.now = 2.0
    refusal = _refusal(limiter)

    # Assert: waiting out the minute alone would not help
    assert refusal.retry_after_seconds == int(DAY - 2.0)


def test_limits_are_read_at_call_time(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=1, per_day=40)
    limiter = _limiter(FakeClock())
    limiter.check_and_record(DEVICE)
    _refusal(limiter)

    # Act: an admin raises the limit without a restart
    _limits(set_setting, per_minute=2, per_day=40)
    limiter.check_and_record(DEVICE)

    # Assert
    assert _refusal(limiter).retry_after_seconds >= 1


def test_devices_are_counted_separately(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=1, per_day=40)
    limiter = _limiter(FakeClock())
    limiter.check_and_record(DEVICE)

    # Act
    limiter.check_and_record("another-device-0123456789abcdef")

    # Assert
    _refusal(limiter)


def test_raw_device_code_is_never_stored(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    limiter = _limiter(FakeClock())

    # Act
    limiter.check_and_record(DEVICE)

    # Assert: neither as a key nor anywhere in the object's reachable state
    state = vars(limiter)
    keys = [k for value in state.values() if isinstance(value, dict) for k in value]
    assert keys, "the limiter should hold one keyed window"
    for key in keys:
        assert isinstance(key, bytes)
        assert DEVICE.encode() not in key
    assert DEVICE not in repr(state)
    assert DEVICE.encode() not in repr(state).encode()


def test_keys_are_salted_per_instance(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    first = _limiter(FakeClock())
    second = _limiter(FakeClock())

    # Act
    first.check_and_record(DEVICE)
    second.check_and_record(DEVICE)

    # Assert: the same device maps to a different key in each process
    first_keys = {k for v in vars(first).values() if isinstance(v, dict) for k in v}
    second_keys = {k for v in vars(second).values() if isinstance(v, dict) for k in v}
    assert first_keys
    assert first_keys.isdisjoint(second_keys)


def test_idle_devices_are_pruned_after_a_day(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    clock = FakeClock(0.0)
    limiter = _limiter(clock)
    limiter.check_and_record("first-device-0123456789abcdef")
    assert len(limiter) == 1

    # Act: a day and a second later another device arrives
    clock.now = DAY + 1.0
    limiter.check_and_record("second-device-0123456789abcdef")

    # Assert
    assert len(limiter) == 1


def test_a_device_seen_within_a_day_is_kept(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    clock = FakeClock(0.0)
    limiter = _limiter(clock)
    limiter.check_and_record("first-device-0123456789abcdef")

    # Act
    clock.now = DAY - 1.0
    limiter.check_and_record("second-device-0123456789abcdef")

    # Assert
    assert len(limiter) == 2


def test_state_is_bounded_and_the_oldest_keys_are_dropped(
    set_setting: SettingPatcher,
) -> None:
    # Arrange: room for three devices, one hit per minute each
    _limits(set_setting, per_minute=1, per_day=40)
    clock = FakeClock(0.0)
    limiter = _limiter(clock, max_keys=3)
    devices = [f"device-{i}-0123456789abcdef" for i in range(5)]

    # Act
    for i, device in enumerate(devices):
        clock.now = float(i)
        limiter.check_and_record(device)

    # Assert: only the three newest remain, and the oldest starts afresh
    assert len(limiter) == 3
    clock.now = 10.0
    limiter.check_and_record(devices[0])
    assert len(limiter) == 3
    _refusal(limiter, devices[4])


# ── per-address windows and the fixed-reply ceiling (plan 14.7) ──────────────

ADDRESS = "203.0.113.7"


def _address_limits(set_setting: SettingPatcher, per_minute: int, per_day: int) -> None:
    set_setting("PRIEST_RATE_LIMIT_PER_IP_PER_MINUTE", per_minute)
    set_setting("PRIEST_RATE_LIMIT_PER_IP_PER_DAY", per_day)


def test_a_client_varying_its_device_is_still_limited_by_address(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    _address_limits(set_setting, per_minute=3, per_day=100)
    limiter = _limiter(FakeClock())
    for n in range(3):
        limiter.check_and_record(f"device-{n:024d}", ADDRESS)

    # Act
    try:
        limiter.check_and_record("device-fresh-0123456789abcd", ADDRESS)
    except PriestRateLimitError as exc:
        refusal = exc
    else:
        raise AssertionError("expected the address window to refuse")

    # Assert
    assert 1 <= refusal.retry_after_seconds <= 60


def test_without_an_address_only_the_device_limit_applies(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    _address_limits(set_setting, per_minute=1, per_day=1)
    limiter = _limiter(FakeClock())

    # Act — five devices, no address known
    for n in range(5):
        limiter.check_and_record(f"device-{n:024d}")

    # Assert
    assert len(limiter) == 5


def test_a_hit_refused_by_the_address_is_not_counted_for_the_device(
    set_setting: SettingPatcher,
) -> None:
    # Arrange — the address is full; the device has never asked
    _limits(set_setting, per_minute=1, per_day=40)
    _address_limits(set_setting, per_minute=1, per_day=100)
    limiter = _limiter(FakeClock())
    limiter.check_and_record("device-other-0123456789abcd", ADDRESS)
    try:
        limiter.check_and_record(DEVICE, ADDRESS)
    except PriestRateLimitError:
        pass

    # Act — the same device from another address
    limiter.check_and_record(DEVICE, "198.51.100.1")

    # Assert — the refused hit did not use up the device's one question
    _refusal(limiter)


def test_devices_and_addresses_never_share_a_window(
    set_setting: SettingPatcher,
) -> None:
    # Arrange — a device header that happens to equal an address string
    _limits(set_setting, per_minute=1, per_day=40)
    _address_limits(set_setting, per_minute=1, per_day=100)
    limiter = _limiter(FakeClock())
    limiter.check_and_record(ADDRESS)

    # Act
    limiter.check_and_record("device-other-0123456789abcd", ADDRESS)

    # Assert
    assert len(limiter) == 3


def test_the_address_is_never_stored(set_setting: SettingPatcher) -> None:
    # Arrange
    _limits(set_setting, per_minute=4, per_day=40)
    limiter = _limiter(FakeClock())

    # Act
    limiter.check_and_record(DEVICE, ADDRESS)

    # Assert
    assert ADDRESS not in repr(vars(limiter))
    assert ADDRESS.encode() not in repr(vars(limiter)).encode()


def test_fixed_replies_have_a_ceiling_of_their_own(set_setting: SettingPatcher) -> None:
    # Arrange — the question limit is used up
    _limits(set_setting, per_minute=1, per_day=40)
    limiter = _limiter(FakeClock())
    limiter.check_and_record(DEVICE)
    _refusal(limiter)

    # Act — fixed replies still go through, up to their own ceiling
    for _ in range(FIXED_REPLY_DEVICE_LIMITS.per_minute):
        limiter.check_and_record(DEVICE, fixed_reply=True)
    try:
        limiter.check_and_record(DEVICE, fixed_reply=True)
    except PriestRateLimitError as exc:
        refusal = exc
    else:
        raise AssertionError("expected the fixed-reply ceiling to refuse")

    # Assert
    assert 1 <= refusal.retry_after_seconds <= 60


def test_fixed_replies_do_not_use_up_the_question_limit(
    set_setting: SettingPatcher,
) -> None:
    # Arrange
    _limits(set_setting, per_minute=1, per_day=40)
    limiter = _limiter(FakeClock())
    for _ in range(10):
        limiter.check_and_record(DEVICE, ADDRESS, fixed_reply=True)

    # Act / Assert — the one question is still available
    limiter.check_and_record(DEVICE, ADDRESS)
    _refusal(limiter)


def test_the_fixed_reply_address_ceiling_is_wider_than_the_device_one() -> None:
    # An office shares one address; a flood from one device should hit first
    assert FIXED_REPLY_ADDRESS_LIMITS.per_minute > FIXED_REPLY_DEVICE_LIMITS.per_minute
    assert FIXED_REPLY_ADDRESS_LIMITS.per_day > FIXED_REPLY_DEVICE_LIMITS.per_day
