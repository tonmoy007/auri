"""Tests for the suite's network guard.

No ordinary test may reach a real model server (Ollama, vLLM) or anything else over
the network: such a test is slow, loads gigabytes of model into a developer's memory and
passes or fails on what happens to be running. A connection attempt fails the test
that made it, loudly, because the LLM code catches its own errors and would
otherwise hide the leak.
"""

from __future__ import annotations

import socket

import pytest

from tests import network_guard


def test_a_real_connection_attempt_is_refused_and_recorded(
    network_violations: list[str],
) -> None:
    # Act
    with pytest.raises(network_guard.NetworkBlockedError):
        socket.create_connection(("127.0.0.1", 11434), timeout=1)

    # Assert — recorded, so the test is failed at teardown even if code swallowed the error
    assert network_violations == ["127.0.0.1:11434"]
    network_violations.clear()


def test_a_unix_socket_pair_still_works(network_violations: list[str]) -> None:
    # Arrange / Act — asyncio itself uses these
    left, right = socket.socketpair()
    left.close()
    right.close()

    # Assert
    assert network_violations == []


def test_the_violation_message_names_the_target_and_the_remedy() -> None:
    # Act
    message = str(network_guard.NetworkBlockedError("127.0.0.1:11434"))

    # Assert
    assert "127.0.0.1:11434" in message
    assert "live_llm" in message


@pytest.mark.live_llm
def test_a_live_test_is_skipped_unless_asked_for() -> None:
    # Assert — reaching here means RUN_LIVE_LLM=1 was set on purpose
    assert network_guard.live_tests_enabled()
