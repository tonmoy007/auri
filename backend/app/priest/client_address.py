"""The client's address for the Guide's per-address limit (plan 14.7).

Only a header the deployment names in ``TRUSTED_PROXY_HEADER`` is read, never
``X-Forwarded-For`` on its own say: any client can send that header with any value,
so trusting it unconfigured would let a client pick its own address. With nothing
configured there is no address, and only the device limit applies.

When the named header holds a list (``X-Forwarded-For`` style), only the last entry
is used: it is the one the trusted proxy itself appended; earlier entries came from
the client. An IPv6 address is reduced to its /64, because one subscriber is usually
handed a whole /64 and could otherwise rotate through it.

The address is used only as a limiter key (salted and hashed there) and is never
logged or stored.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Final

_IPV6_SUBSCRIBER_PREFIX: Final = 64


def client_address(headers: Mapping[str, str], trusted_header: str) -> str | None:
    """The client's address from *trusted_header*, or ``None``.

    ``None`` when no header is trusted, the header is absent, or its value is not an
    IP address; the caller then applies the device limit alone.
    """
    name = trusted_header.strip()
    if not name:
        return None
    raw = headers.get(name)
    if not raw:
        return None
    candidate = raw.split(",")[-1].strip()
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        network = ipaddress.IPv6Network(
            (address, _IPV6_SUBSCRIBER_PREFIX), strict=False
        )
        return str(network)
    return str(address)
