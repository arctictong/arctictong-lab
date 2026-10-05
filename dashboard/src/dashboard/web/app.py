"""The dashboard's HTTP surface.

Three panels live here -- nodes, services and Proxmox -- all read-only, all of
which answer with JSON when the upstream is broken.  An ops page that turns into
an HTML stack trace at 3am is worse than one that says ``{"error": ...}`` in the
same shape it always has.

Routes:

===============================  ==========================================
``GET /api/health``              liveness of the dashboard itself, no
                                 upstream touched (this is what tsguard polls)
``GET /api/nodes``               headscale node list + online/last seen/tags
``GET /api/services``            one probe per configured service
``GET /api/pve/summary``         every container/VM, with its last backup
``GET /dashboard/``              the HTML page, all three panels
``GET /``                        redirect to ``/dashboard/``
===============================  ==========================================

nginx in front of this proxies ``/api/`` and ``/dashboard/`` to port 8001 and
serves the old headscale UI's static files from disk, so the SvelteKit build
keeps working untouched at ``/web/``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..collector import headscale, pve, services
from ..config import get_settings
from ..models.pve import humanize_uptime

log = logging.getLogger(__name__)

APP = FastAPI(title="Ops Dashboard", version="0.1.0", docs_url=None, redoc_url=None)
router = APIRouter()

# Templates live inside the package rather than beside it.  Deployed, the app is
# run straight out of /opt/dashboard/releases/<stamp>/dashboard/, so a path that
# climbs out of the package would have to know how deep the release happens to
# sit -- and would break the moment that changed.
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
if not TEMPLATES_DIR.is_dir():
    raise RuntimeError(f"templates directory missing: {TEMPLATES_DIR}")
TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))

#: The page prints uptimes; the model owns how they read.  A global rather than
#: a value baked into each row, so the API keeps the spec's raw ``uptime_sec``.
TEMPLATES.env.globals["human_uptime"] = humanize_uptime

#: How often the page re-fetches, in milliseconds.  Matches the slowest sensible
#: probe timeout so the page never shows a row newer than the data behind it.
REFRESH_MS = 15_000


def _now_rfc3339() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _headscale_client():
    """Build a client from the key *file*, never from the environment.

    The key is read here rather than passed in argv or env because env shows up
    in ``/proc/*/environ`` to anyone on the box.
    """
    cfg = get_settings()
    try:
        key = Path(cfg.headscale_key_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FileNotFoundError(f"cannot read {cfg.headscale_key_path}: {exc}") from exc
    if not key:
        raise ValueError(f"{cfg.headscale_key_path} is empty")

    return headscale.HeadscaleClient(
        url=cfg.headscale_url,
        api_key=key,
        timeout=cfg.headscale_timeout,
        verify_tls=cfg.headscale_verify_tls,
    )


def _node_row(node: headscale.HeadscaleNode) -> dict[str, object]:
    return {
        "id": node.id,
        "name": node.display_name,
        "online": node.online,
        # A method, not a property: without the call this dict holds a bound
        # method and JSONResponse fails on it -- which is exactly how the first
        # real deploy answered /api/nodes with an HTML 500.
        "last_seen": node.last_seen_human(),
        "ip": node.ip4,
        # headscale's v1 Node has no OS field.  The spec asked for one; it stays
        # null rather than being guessed from the hostname, which would be a lie
        # that outlives the guess.
        "os": None,
        "tags": node.tags,
        "key_expiry": node.expiry,
    }


async def _collect_nodes() -> tuple[list[dict[str, object]], str | None]:
    try:
        nodes = await _headscale_client().fetch_nodes()
    except FileNotFoundError as exc:
        return [], str(exc)
    except headscale.HeadscaleError as exc:
        return [], str(exc)

    rows = [_node_row(n) for n in nodes.values()]
    rows.sort(key=lambda r: str(r["name"]).lower())
    return rows, None


def _pve_client() -> pve.PveClient:
    """Build a client from the token *file*, never from the environment.

    Same reasoning as :func:`_headscale_client`: env shows up in
    ``/proc/*/environ`` to anyone on the box, so neither secret is read from it.
    """
    cfg = get_settings()
    try:
        secret = Path(cfg.pve_token_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise FileNotFoundError(f"cannot read {cfg.pve_token_path}: {exc}") from exc
    if not secret:
        raise FileNotFoundError(f"{cfg.pve_token_path} is empty")

    return pve.PveClient(
        url=cfg.pve_url,
        token_id=cfg.pve_token_id,
        token_secret=secret,
        timeout=cfg.pve_timeout,
        verify_tls=cfg.pve_verify_tls,
    )


async def _collect_pve() -> tuple[list[dict[str, object]], str | None, int | None]:
    """Guests, an error string, and PVE's backup-job count (``None`` = unknown)."""
    cfg = get_settings()
    try:
        client = _pve_client()
    except FileNotFoundError as exc:
        return [], str(exc), None

    try:
        rows, backup_jobs = await pve.collect_summary(
            client,
            fresh_seconds=cfg.pve_backup_fresh_hours * 3600,
        )
    except pve.PveError as exc:
        return [], str(exc), None
    return rows, None, backup_jobs


@router.get("/api/health")
async def health() -> dict[str, object]:
    cfg = get_settings()
    return {
        "status": "ok",
        "service": "dashboard",
        "nodes_configured": True,
        "services_configured": len(cfg.services),
        "services_error": cfg.services_error,
        "generated_at": _now_rfc3339(),
    }


@router.get("/api/nodes")
async def api_nodes() -> JSONResponse:
    rows, error = await _collect_nodes()
    body: dict[str, object] = {"nodes": rows, "generated_at": _now_rfc3339()}
    if error:
        # Still 200 with an empty list: the caller can tell "no nodes" from
        # "could not ask" by the error field, and a monitor watching this
        # endpoint does not flap on a transient headscale hiccup.
        body["error"] = error
    return JSONResponse(content=body)


@router.get("/api/services")
async def api_services() -> JSONResponse:
    cfg = get_settings()
    body: dict[str, object] = {"generated_at": _now_rfc3339()}
    if cfg.services_error:
        body["services"] = []
        body["error"] = f"cannot read {cfg.services_source}: {cfg.services_error}"
        return JSONResponse(content=body)

    results = await services.probe_all(cfg.services)
    body["services"] = [r.as_dict() for r in results]
    body["summary"] = {
        "up": sum(1 for r in results if r.state == "up"),
        "warn": sum(1 for r in results if r.state == "warn"),
        "down": sum(1 for r in results if r.state == "down"),
    }
    return JSONResponse(content=body)


@router.get("/api/pve/summary")
async def api_pve_summary() -> JSONResponse:
    rows, error, backup_jobs = await _collect_pve()
    body: dict[str, object] = {"guests": rows, "generated_at": _now_rfc3339()}
    # Present only when PVE answered.  The page uses this to explain a wall of
    # red dots; an unknown count must never be rendered as "no jobs".
    if backup_jobs is not None:
        body["backup_jobs"] = backup_jobs
    if error:
        body["error"] = error
    return JSONResponse(content=body)


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/", status_code=302)


@router.get("/dashboard/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(request: Request) -> HTMLResponse:
    node_rows, node_error = await _collect_nodes()
    pve_rows, pve_error, pve_backup_jobs = await _collect_pve()

    cfg = get_settings()
    svc_results = []
    if not cfg.services_error:
        svc_results = await services.probe_all(cfg.services)

    return TEMPLATES.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "nodes": node_rows,
            "node_error": node_error,
            "services": [r.as_dict() for r in svc_results],
            "services_error": cfg.services_error,
            "services_source": cfg.services_source,
            "pve_guests": pve_rows,
            "pve_error": pve_error,
            "pve_backup_jobs": pve_backup_jobs,
            "refresh_ms": REFRESH_MS,
            "generated_at": _now_rfc3339(),
        },
    )


APP.include_router(router)


@APP.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Answer in JSON even for a bug in this application.

    The promise this module opens with -- "an ops page that turns into an HTML
    stack trace at 3am is worse than one that says ``{"error": ...}``" -- has to
    hold for our own failures too, not just headscale's.  The traceback still
    goes to the log; only the class name crosses the wire.
    """
    log.error("unhandled error serving %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": f"internal error: {type(exc).__name__}",
            "generated_at": _now_rfc3339(),
        },
    )


def main() -> None:
    """Entry point for the systemd unit on CT 109."""
    import uvicorn

    uvicorn.run(
        "dashboard.web.app:APP",
        host=os.environ.get("DASHBOARD_BIND", "127.0.0.1"),
        port=int(os.environ.get("DASHBOARD_PORT", "8001")),
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
