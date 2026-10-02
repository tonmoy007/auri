"""Tests for reading the client's address from a trusted proxy header (plan 14.7)."""

from __future__ import annotations

import pytest
from app.priest.client_address import client_address


def test_no_trusted_header_means_no_address() -> None:
    # A client can send X-Forwarded-For with anything in it; unconfigured, it is ignored
    assert client_address({"X-Forwarded-For": "203.0.113.7"}, "") is None
    assert client_address({"X-Forwarded-For": "203.0.113.7"}, "   ") is None


def test_the_trusted_header_is_read() -> None:
    assert client_address({"X-Real-IP": "203.0.113.7"}, "X-Real-IP") == "203.0.113.7"


def test_an_untrusted_header_is_ignored() -> None:
    headers = {"X-Forwarded-For": "198.51.100.9"}
    assert client_address(headers, "X-Real-IP") is None


def test_only_the_entry_the_proxy_appended_is_used() -> None:
    # The client wrote the first entry; the trusted proxy appended the last
    headers = {"X-Forwarded-For": "10.9.9.9, 1.2.3.4, 203.0.113.7"}
    assert client_address(headers, "X-Forwarded-For") == "203.0.113.7"


@pytest.mark.parametrize("value", ["", "not-an-ip", "203.0.113.7:443", "unknown"])
def test_a_missing_or_unreadable_value_means_no_address(value: str) -> None:
    assert client_address({"X-Real-IP": value}, "X-Real-IP") is None


def test_an_ipv6_address_is_reduced_to_its_subscriber_network() -> None:
    # One subscriber is usually handed a whole /64 and could rotate through it
    first = client_address({"X-Real-IP": "2001:db8:1:2::1"}, "X-Real-IP")
    second = client_address({"X-Real-IP": "2001:db8:1:2:ffff::9"}, "X-Real-IP")
    assert first == second == "2001:db8:1:2::/64"


def test_an_ipv4_mapped_address_counts_as_ipv4() -> None:
    headers = {"X-Real-IP": "::ffff:203.0.113.7"}
    assert client_address(headers, "X-Real-IP") == "203.0.113.7"
