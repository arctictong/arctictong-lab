"""Asking one Proxmox host what it is running, read-only.

Two things this module is deliberate about, both learned the hard way:

* **The token never reaches a log line.**  It travels in the ``Authorization``
  header rather than a query string -- a URL lands in nginx's access log and in
  every traceback -- and each message still goes through :func:`redact`, because
  a misbehaving server can echo anything back in a body we quote.
* **The extra question must not sink the panel.**  ``/cluster/backup`` is asked
  only to explain *why* a wall of guests says "no recent backup".  If that call
  is refused, the guests still render and the count is simply absent.

The token is created on the PVE host as ``dashboard@pve`` with the built-in
``PVEAuditor`` role, whose power set has no ``VM.PowerMgmt``: a POST to start
anything answers ``403``.  The dashboard has no start/stop code to guard,
because the spec cut it -- and the token would refuse it anyway.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models.pve import (
    PveGuest,
    PveTask,
    parse_guests,
    parse_tasks,
    recent_backup_vmids,
)

log = logging.getLogger(__name__)

RESOURCES = "cluster/resources"
TASKS = "cluster/tasks"
BACKUP_JOBS = "cluster/backup"


class PveError(Exception):
    """A PVE call that did not produce data.

    ``retryable`` separates "the token is wrong, stop asking" from "the box is
    busy, ask again later", because those want opposite responses.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def redact(text: str, secret: str) -> str:
    """Replace the token value wherever it appears, so it cannot be logged.

    The same three lines as :func:`dashboard.collector.headscale.redact`.  Each
    collector keeps its own copy on purpose: neither should have to import the
    other's secret handling to get its own right.
    """
    return text.replace(secret, "***") if secret else text


class PveClient:
    """Minimal read-only PVE client -- three GETs, hand-rolled on httpx."""

    def __init__(
        self,
        url: str,
        token_id: str,
        token_secret: str,
        timeout: float = 10.0,
        verify_tls: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._token_id = token_id
        self._secret = token_secret
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._client = client

    @property
    def host(self) -> str:
        return self._url

    def _endpoint(self, path: str) -> str:
        return f"{self._url}/api2/json/{path.lstrip('/')}"

    async def aclose(self) -> None:
        """Close the client we were handed, if we were handed one."""
        if self._client is not None:
            await self._client.aclose()

    async def fetch_guests(self) -> list[PveGuest]:
        return parse_guests(await self._get(RESOURCES))

    async def fetch_tasks(self) -> list[PveTask]:
        return parse_tasks(await self._get(TASKS))

    async def fetch_backup_jobs(self) -> int:
        """How many backup jobs are configured.  ``0`` is a real answer here."""
        return len(await self._get(BACKUP_JOBS))

    async def _get(self, path: str) -> list[Any]:
        if self._client is not None:
            return await self._request(self._client, path)
        async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify_tls) as client:
            return await self._request(client, path)

    async def _request(self, client: httpx.AsyncClient, path: str) -> list[Any]:
        url = self._endpoint(path)
        try:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"PVEAPIToken={self._token_id}={self._secret}",
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise PveError(
                redact(f"GET {url} timed out after {self._timeout}s", self._secret)
            ) from exc
        except httpx.HTTPError as exc:
            raise PveError(redact(f"GET {url} failed: {exc}", self._secret)) from exc

        if response.status_code in (401, 403):
            raise PveError(
                f"GET {url} was rejected with {response.status_code} -- "
                "PVE token wrong, or it lacks PVEAuditor",
                retryable=False,
            )
        if response.status_code >= 400:
            raise PveError(
                redact(
                    f"GET {url} returned {response.status_code}: {response.text[:160]}",
                    self._secret,
                )
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise PveError(
                redact(f"GET {url} did not return JSON: {exc}", self._secret)
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise PveError(
                redact(f"GET {url} answered without a 'data' list", self._secret),
                retryable=False,
            )
        return payload["data"]


async def collect_summary(
    client: PveClient,
    *,
    fresh_seconds: float,
    now: float | None = None,
) -> tuple[list[dict[str, object]], int | None]:
    """The panel in one call: every guest, each with its backup verdict.

    Returns the rows and how many backup jobs PVE has configured.  That second
    value is ``None`` when PVE refused the question -- which is also the only
    thing that distinguishes "a job exists but nothing has run" from "no job was
    ever created", the difference between a stale host and an unconfigured one.
    """
    guests = await client.fetch_guests()
    tasks = await client.fetch_tasks()
    recent = recent_backup_vmids(tasks, fresh_seconds=fresh_seconds, now=now)
    rows = [guest.as_dict(backup_recent=guest.vmid in recent) for guest in guests]

    backup_jobs: int | None
    try:
        backup_jobs = await client.fetch_backup_jobs()
    except PveError as exc:
        log.info("pve: cannot count backup jobs: %s", exc)
        backup_jobs = None

    return rows, backup_jobs
