# tsguard

Tailscale/Headscale connection watchdog — watches the tailnet, a Headscale API,
and a hub's WireGuard peer table, alerts over Telegram, and repairs what can be
repaired over `tailscale ssh`.

Everything lives in [`tsguard/`](tsguard/):

- **source** — `tsguard/src/tsguard/` (see `tsguard/pyproject.toml`, run with `uv sync`)
- **docs** — `tsguard/docs/` — start at `phase0-first-run.md`; the WireGuard
  watch is explained in `wireguard-watch.md`
- **deploy** — `tsguard/deploy/` — systemd unit, wrappers, and `tsguard/deploy.ps1`
  for pushing a build to the LXC

```bash
cd tsguard
uv sync
uv run pytest            # 614 passed, 5 skipped
uv run tsguard --help
```
