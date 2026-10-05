"""The nodes panel: parsing headscale's output and the shape it is published as.

The fixtures here are shaped like the real v1 payload -- camelCase, string ids,
RFC3339 timestamps -- because a test built from a tidied-up dict would pass on
a field headscale never sends.

What is worth asserting here: a node that loses a field is still listed, one
malformed record does not blind the panel to the others, a timestamp nobody can
parse becomes "unknown" rather than a 500, and the API key cannot reach a log
line.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from dashboard.collector.headscale import HeadscaleClient, HeadscaleError, parse_nodes, redact
from dashboard.models.nodes import HeadscaleNode, humanize_last_seen

KEY = "super-secret-api-key"

# Trimmed from a real headscale 0.29 /api/v1/node response.  ``os`` is absent
# because headscale does not send it -- the spec asked for it anyway.
NODE_WIRE = {
    "id": "1",
    "machineKey": "mkey:0123456789abcdef",
    "nodeKey": "nodekey:0123456789abcdef",
    "discoKey": "disco:0123456789abcdef",
    "ipAddresses": ["100.64.0.1", "fd7a:115c:a1e0::1"],
    "name": "pchome.lan",
    "givenName": "pchome",
    "user": {
        "id": "1",
        "name": "elib",
        "createdAt": "2026-01-02T03:04:05Z",
        "displayName": "elib",
        "email": "",
        "provider": "local",
    },
    "online": True,
    "lastSeen": "2026-09-26T08:00:00Z",
    "expiry": "2026-12-30T00:00:00Z",
    "createdAt": "2026-01-02T03:04:05Z",
    "registerMethod": "auth_key",
    "approvedRoutes": [],
    "availableRoutes": [],
    "subnetRoutes": [],
    "tags": ["myhome"],
}

NODE_MINIMAL = {
    "id": "2",
    "name": "bare",
    "online": False,
    "lastSeen": None,
    "expiry": None,
}


def serving(handler, status_code: int = 200) -> HeadscaleClient:
    """A client whose transport is a stub -- no network, no real headscale."""
    return HeadscaleClient(
        url="http://hs.invalid/api/v1/",
        api_key=KEY,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="",
        ),
    )


# --- the model ---------------------------------------------------------------


def test_wire_fields_land_on_the_model() -> None:
    node = HeadscaleNode.model_validate(NODE_WIRE)

    assert node.id == "1"
    assert node.name == "pchome.lan"
    assert node.given_name == "pchome"
    assert node.online is True
    assert node.user is not None and node.user.name == "elib"
    assert node.tags == ["myhome"]


def test_ip4_picks_the_v4_address() -> None:
    """The order headscale returns is not guaranteed, so scan for the colon."""
    node = HeadscaleNode.model_validate(NODE_WIRE)
    assert node.ip4 == "100.64.0.1"


def test_ip4_is_none_when_only_v6() -> None:
    node = HeadscaleNode.model_validate({"id": "3", "ipAddresses": ["fd7a:115c:a1e0::9"]})
    assert node.ip4 is None


def test_os_is_null_not_guessed() -> None:
    """headscale v1 has no OS field; inventing one from the hostname would be a lie."""
    node = HeadscaleNode.model_validate(NODE_WIRE)
    assert node.os is None


def test_display_name_prefers_the_given_name_then_the_id() -> None:
    # Ticket 03's shape sample is "name": "pchome" for a node headscale reports
    # as name="pchome.lan" -- the given name is the label to show.  The real
    # case is starker: name="DESKTOP-CDFJ4TV", givenName="pchome".
    assert HeadscaleNode.model_validate(NODE_WIRE).display_name == "pchome"
    assert HeadscaleNode.model_validate({"id": "4", "name": "raw-host"}).display_name == "raw-host"
    assert HeadscaleNode.model_validate({"id": "5"}).display_name == "5"
    assert HeadscaleNode.model_validate({"id": "6", "givenName": "   "}).display_name == "6"


def test_unknown_fields_are_ignored_not_fatal() -> None:
    """0.30 adding a column must not empty the panel."""
    node = HeadscaleNode.model_validate({**NODE_WIRE, "somethingNew": {"nested": 1}})
    assert node.id == "1"


def test_missing_optional_fields_default() -> None:
    node = HeadscaleNode.model_validate(NODE_MINIMAL)
    assert node.ip_addresses == []
    assert node.tags == []
    assert node.user is None
    assert node.online is False


# --- lastSeen ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-26T08:00:00Z", "3m ago"),
        ("2026-09-26T07:59:30Z", "3m ago"),
        ("2026-09-26T08:00:55Z", "2m ago"),
        ("2026-09-26T08:02:30Z", "30s ago"),
        ("2026-09-26T05:00:00Z", "3h 3m ago"),
        ("2026-09-22T08:00:00Z", "4d 0h ago"),
    ],
)
def test_humanize_last_seen(value: str, expected: str) -> None:
    now = datetime(2026, 9, 26, 8, 3, 0, tzinfo=UTC)
    assert humanize_last_seen(value, now) == expected


def test_humanize_accepts_offset_suffix() -> None:
    now = datetime(2026, 9, 26, 8, 3, 0, tzinfo=UTC)
    assert humanize_last_seen("2026-09-26T08:00:00+00:00", now) == "3m ago"


def test_humanize_clock_skew_reads_as_now_not_negative() -> None:
    """A phone whose clock runs fast must not render "-2m ago"."""
    now = datetime(2026, 9, 26, 8, 0, 0, tzinfo=UTC)
    assert humanize_last_seen("2026-09-26T08:02:00Z", now) == "just now"


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45T99:99:99Z"])
def test_humanize_unparseable_is_none(value: str | None) -> None:
    """Unknown, not an exception -- one bad timestamp must not 500 the panel."""
    assert humanize_last_seen(value) is None


# --- parse_nodes -------------------------------------------------------------


def test_parse_nodes_accepts_a_bare_array() -> None:
    nodes, skipped = parse_nodes([NODE_WIRE, NODE_MINIMAL])
    assert sorted(nodes) == ["1", "2"]
    assert skipped == 0


def test_parse_nodes_accepts_a_wrapped_object() -> None:
    nodes, _ = parse_nodes({"nodes": [NODE_WIRE]})
    assert list(nodes) == ["1"]


def test_parse_nodes_skips_a_malformed_record_and_keeps_the_rest() -> None:
    """The whole point: one bad node must not cost the panel every other node."""
    nodes, skipped = parse_nodes([NODE_WIRE, "not-a-dict", {"name": "no id"}, None])
    assert list(nodes) == ["1"]
    assert skipped == 3


def test_parse_nodes_rejects_a_shape_it_cannot_read() -> None:
    with pytest.raises(HeadscaleError):
        parse_nodes("totally not json nodes")
    with pytest.raises(HeadscaleError):
        parse_nodes({"unexpected": "object"})


# --- the client --------------------------------------------------------------


async def test_fetch_nodes_sends_bearer_key_and_returns_nodes() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[NODE_WIRE])

    nodes = await serving(handler).fetch_nodes()

    assert list(nodes) == ["1"]
    assert seen["auth"] == f"Bearer {KEY}"
    # A trailing slash on the configured URL must not double up.
    assert seen["url"] == "http://hs.invalid/api/v1/api/v1/node"


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(401, False), (403, False), (500, True), (502, True)],
)
async def test_client_classifies_status_codes(status: int, retryable: bool) -> None:
    """401 means stop asking; 5xx means ask again later."""
    with pytest.raises(HeadscaleError) as caught:
        await serving(lambda _r: httpx.Response(status, text="x")).fetch_nodes()
    assert caught.value.retryable is retryable


async def test_client_reports_non_json() -> None:
    with pytest.raises(HeadscaleError, match="JSON"):
        await serving(lambda _r: httpx.Response(200, text="<html>")).fetch_nodes()


async def test_client_reports_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(HeadscaleError, match="timed out"):
        await serving(handler, ).fetch_nodes()


# --- the key cannot leak -----------------------------------------------------


def test_redact_removes_the_key() -> None:
    assert redact("failed with hsapiabc123", "hsapiabc123") == "failed with ***"


def test_redact_with_no_secret_is_a_passthrough() -> None:
    assert redact("nothing to hide", "") == "nothing to hide"


async def test_key_never_appears_in_a_raised_message() -> None:
    """httpx puts the URL in exception text, so every message is filtered."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.url} auth={KEY}", request=request)

    with pytest.raises(HeadscaleError) as caught:
        await serving(handler).fetch_nodes()

    assert KEY not in str(caught.value)
    assert KEY not in repr(caught.value)
