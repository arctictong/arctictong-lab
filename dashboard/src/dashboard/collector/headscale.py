"""Reading headscale's node list over the v1 REST API.

Two properties this module is deliberate about:

* **The API key never reaches a log line.**  It travels in a header rather than
  the URL so a traceback cannot leak it, and every message is still filtered
  through :func:`redact` because httpx puts request URLs into exception text
  and a server can echo anything back.
* **One bad node does not blind the panel to the rest.**  A node that fails
  validation is counted and skipped; it never fails the whole list.  A
  monitoring tool that shows "0 nodes, error" when one record has a new field
  is worse than one that shows 15 nodes and logs the odd one.

The gRPC API that headscale used to have is gone as of #3324 -- REST at
``/api/v1`` is the only door, which is why the dashboard holds a key at all.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import ValidationError

from ..models.nodes import HeadscaleNode

log = logging.getLogger(__name__)


class HeadscaleError(Exception):
    """A headscale call that did not produce a node list.

    ``retryable`` separates "your key is wrong, stop asking" from "the box is
    busy, ask again later", because the two want opposite responses.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def redact(text: str, secret: str) -> str:
    """Replace the key wherever it appears, so it cannot be logged by accident."""
    return text.replace(secret, "***") if secret else text


def parse_nodes(payload: Any) -> tuple[dict[str, HeadscaleNode], int]:
    """Turn a decoded v1 response into nodes keyed by id, plus a skip count."""
    if isinstance(payload, dict):
        for key in ("nodes", "Nodes"):
            inner = payload.get(key)
            if isinstance(inner, list):
                payload = inner
                break
        else:
            raise HeadscaleError(
                f"expected a list of nodes, got an object with keys {sorted(payload)[:8]}",
                retryable=False,
            )
    if not isinstance(payload, list):
        raise HeadscaleError(
            f"expected a JSON array of nodes, got {type(payload).__name__}",
            retryable=False,
        )

    out: dict[str, HeadscaleNode] = {}
    skipped = 0
    for entry in payload:
        try:
            node = HeadscaleNode.model_validate(entry)
        except ValidationError as exc:
            skipped += 1
            log.warning("skipping unparseable headscale node: %s", exc.errors()[:2])
            continue
        if not node.id:
            skipped += 1
            log.warning("skipping headscale node with no id")
            continue
        out[node.id] = node

    return out, skipped


class HeadscaleClient:
    """Minimal v1 client -- one endpoint, hand-rolled on httpx.

    A vendored SDK would add a dependency tree to patch for a single GET.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: float = 10.0,
        verify_tls: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._client = client

    @property
    def host(self) -> str:
        return self._url

    def _endpoint(self, path: str) -> str:
        return f"{self._url}/{path.lstrip('/')}"

    async def fetch_nodes(self) -> dict[str, HeadscaleNode]:
        if self._client is not None:
            return await self._get(self._client)
        async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify_tls) as client:
            return await self._get(client)

    async def _get(self, client: httpx.AsyncClient) -> dict[str, HeadscaleNode]:
        url = self._endpoint("api/v1/node")
        try:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise HeadscaleError(
                redact(f"GET {url} timed out after {self._timeout}s", self._api_key),
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise HeadscaleError(
                redact(f"GET {url} failed: {exc}", self._api_key), retryable=True
            ) from exc

        if response.status_code in (401, 403):
            raise HeadscaleError(
                f"GET {url} was rejected with {response.status_code} -- "
                "API key wrong or lacks permission",
                retryable=False,
            )
        if response.status_code >= 400:
            raise HeadscaleError(
                redact(f"GET {url} returned {response.status_code}", self._api_key),
                retryable=True,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise HeadscaleError(
                redact(f"GET {url} did not return JSON: {exc}", self._api_key),
                retryable=True,
            ) from exc

        nodes, skipped = parse_nodes(payload)
        log.info(
            "headscale: %d node(s) from %s%s",
            len(nodes),
            self.host,
            f", {skipped} skipped" if skipped else "",
        )
        return nodes
