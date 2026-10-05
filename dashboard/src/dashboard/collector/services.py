"""Probing the services NPM fronts.

The question this answers is not "is the service healthy" but "would a person
opening the dashboard be surprised" -- so the three answers are:

``up``
    2xx, fast enough.
``warn``
    The service *answered* and the answer was not a clean success: a 4xx (it is
    up and refusing us), a 3xx, or a 2xx that took longer than
    ``warn_latency_ms``.  Worth a look, not an outage.
``down``
    It could not be reached at all (DNS, refused, TLS, timeout), or it replied
    with a status in ``down_status`` (502/503/504 -- a gateway saying the thing
    behind it is gone).

That split is why 5xx is not blanket ``down``: a 500 from a service that
answered in 30 ms is a bug in the service, which is exactly the sort of thing
that should be yellow and noticed rather than red and ignored.  A 503 from NPM
means the upstream is gone, which *is* an outage.  ``down_status`` is per-service
configurable because only the owner knows which of those is which for his box.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from ..config import Service

log = logging.getLogger(__name__)

State = str  # "up" | "warn" | "down"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    url: str
    state: State
    latency_ms: int
    status_code: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        """The wire shape from the spec, plus enough to debug a yellow row."""
        return {
            "name": self.name,
            "url": self.url,
            "state": self.state,
            "latency_ms": self.latency_ms,
            "status_code": self.status_code,
            "error": self.error,
        }


def classify(status_code: int, latency_ms: int, service: Service) -> State:
    """Decide up/warn/down from a response we did get."""
    if status_code in service.down_status:
        return "down"
    if not 200 <= status_code < 300:
        return "warn"
    if latency_ms > service.warn_latency_ms:
        return "warn"
    return "up"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


async def probe_tcp(service: Service) -> ProbeResult:
    """Open a TCP connection and close it, speaking no protocol.

    That is the whole check, and for these two targets it is the honest one:
    rustdesk-server's rendezvous ports and hubrelay's status port.  The status
    port wants a bearer token and answers ``401`` without it -- which
    :func:`classify` would call a ``warn``, i.e. a permanent yellow row meaning
    "we did not bring a token", not "the relay is down".  A completed connect is
    the fact that actually matters: something is listening.
    """
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(service.host, service.port),
            timeout=service.timeout,
        )
    except TimeoutError:
        return ProbeResult(
            name=service.name,
            url=service.url,
            state="down",
            latency_ms=_elapsed_ms(started),
            error=f"timeout after {service.timeout:g}s",
        )
    except OSError as exc:
        # ConnectionRefusedError, gaierror, ENETUNREACH -- all "nothing there".
        return ProbeResult(
            name=service.name,
            url=service.url,
            state="down",
            latency_ms=_elapsed_ms(started),
            error=f"{type(exc).__name__}: {exc}"[:120],
        )

    latency = _elapsed_ms(started)
    writer.close()
    # The peer may have hung up first; the connect already succeeded.
    with contextlib.suppress(OSError):
        await writer.wait_closed()

    return ProbeResult(
        name=service.name,
        url=service.url,
        state="warn" if latency > service.warn_latency_ms else "up",
        latency_ms=latency,
    )


async def probe_one(client: httpx.AsyncClient, service: Service) -> ProbeResult:
    if service.is_tcp:
        return await probe_tcp(service)

    started = time.perf_counter()
    try:
        response = await client.get(service.url, timeout=service.timeout, follow_redirects=True)
    except httpx.TimeoutException:
        return ProbeResult(
            name=service.name,
            url=service.url,
            state="down",
            latency_ms=_elapsed_ms(started),
            error=f"timeout after {service.timeout:g}s",
        )
    except httpx.HTTPError as exc:
        # Shortened so a row on the dashboard stays one line.
        return ProbeResult(
            name=service.name,
            url=service.url,
            state="down",
            latency_ms=_elapsed_ms(started),
            error=f"{type(exc).__name__}: {exc}"[:120],
        )

    latency = _elapsed_ms(started)
    return ProbeResult(
        name=service.name,
        url=service.url,
        state=classify(response.status_code, latency, service),
        latency_ms=latency,
        status_code=response.status_code,
    )


async def probe_all(
    services: Iterable[Service],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[ProbeResult]:
    """Probe every service at once.

    Concurrent on purpose: the dashboard's value is that one dead service does
    not delay the answer about the other six.  Sequential probes would make the
    page's load time the sum of every timeout.
    """
    services = list(services)
    if not services:
        return []

    if client is not None:
        return list(await asyncio.gather(*(probe_one(client, s) for s in services)))

    # One client for all probes so connections are pooled and TLS is verified
    # once per host rather than once per service.
    async with httpx.AsyncClient(verify=True) as shared:
        return list(await asyncio.gather(*(probe_one(shared, s) for s in services)))
