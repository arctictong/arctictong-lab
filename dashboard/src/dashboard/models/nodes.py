"""headscale v1 ``Node`` as it actually arrives, and the fields the dashboard needs.

The wire shape is headscale's, not ours: v1 emits camelCase and string ids, and
it grows fields between releases.  ``extra="ignore"`` is therefore a feature --
a monitoring tool that crashes because 0.30 added a column is worse than one
that ignores it.

What this module does *not* do is invent data.  The spec asked for ``os`` on
each node; headscale's Node has no such field, so
:meth:`HeadscaleNode.os` returns ``None`` and the page renders a dash.  Guessing
from the hostname would put a confident wrong answer in a health dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def parse_rfc3339(value: str | None) -> datetime | None:
    """Parse a headscale timestamp.

    Tolerates ``Z``, ``+00:00`` and a bare date, and returns ``None`` rather
    than raising: a node whose lastSeen is malformed is a row that says "unknown",
    not a 500 for the whole panel.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def humanize_last_seen(value: str | None, now: datetime | None = None) -> str | None:
    """``lastSeen`` as a person reads it: ``"3h 12m ago"``.

    Negative deltas happen (clock skew between a phone and the server) and are
    clamped to zero rather than shown as "-2m ago".
    """
    parsed = parse_rfc3339(value)
    if parsed is None:
        return None

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    seconds = int((now.astimezone(UTC) - parsed).total_seconds())
    if seconds <= 0:
        return "just now"

    minutes, sec = divmod(seconds, 60)
    if minutes < 1:
        return f"{sec}s ago"
    hours, minutes = divmod(minutes, 60)
    if hours < 1:
        return f"{minutes}m ago"
    days, hours = divmod(hours, 24)
    if days < 1:
        return f"{hours}h {minutes}m ago"
    return f"{days}d {hours}h ago"


class HeadscaleUser(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = ""
    name: str = ""
    display_name: str = Field(default="", alias="displayName")
    email: str = ""
    provider: str = ""


class HeadscaleNode(BaseModel):
    """One node as headscale v1 reports it.

    Every field has a default, so a node that lost a key is still listed rather
    than dropped -- an unparseable node is skipped by the collector, and a
    skipped node is a node nobody can see.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = ""
    machine_key: str = Field(default="", alias="machineKey")
    node_key: str = Field(default="", alias="nodeKey")
    disco_key: str = Field(default="", alias="discoKey")
    ip_addresses: list[str] = Field(default_factory=list, alias="ipAddresses")
    name: str = ""
    given_name: str = Field(default="", alias="givenName")
    user: HeadscaleUser | None = None
    online: bool = False
    last_seen: str | None = Field(default=None, alias="lastSeen")
    expiry: str | None = None
    created_at: str | None = Field(default=None, alias="createdAt")
    register_method: str = Field(default="", alias="registerMethod")
    approved_routes: list[str] = Field(default_factory=list, alias="approvedRoutes")
    available_routes: list[str] = Field(default_factory=list, alias="availableRoutes")
    subnet_routes: list[str] = Field(default_factory=list, alias="subnetRoutes")
    tags: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Best human label: what the owner named the node, not its hostname.

        headscale carries both.  ``given_name`` is the label the owner chose --
        it is also what the tailnet's MagicDNS shows, so it is the name the owner
        recognises.  ``name`` is the hostname the client reported, which on this
        tailnet is things like ``DESKTOP-CDFJ4TV``, ``097184-W`` and, for one
        iPad, ``localhost``.  Ticket 03's shape sample is ``"name": "pchome"``
        for a node headscale reports as ``name: "pchome.lan"``, so the given name
        is the one to prefer.  ``user.display_name`` and then the id follow, so a
        row is never blank.
        """
        candidates = (self.given_name, self.name, self.user.display_name if self.user else "")
        for candidate in candidates:
            if candidate and candidate.strip():
                return candidate.strip()
        return self.id or "(unnamed)"

    @property
    def ip4(self) -> str | None:
        """The first IPv4 address, since that is the one a person would ping."""
        for addr in self.ip_addresses:
            if ":" not in addr:
                return addr
        return None

    @property
    def os(self) -> str | None:
        """Always ``None``: headscale v1 does not report a node's OS.

        Kept as a property so the shape the dashboard publishes has the field
        the spec asked for, and the reason it is null lives next to the answer.
        """
        return None

    def last_seen_human(self, now: datetime | None = None) -> str | None:
        return humanize_last_seen(self.last_seen, now)
