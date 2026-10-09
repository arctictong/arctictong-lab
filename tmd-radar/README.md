# tmd-radar — TMD radar & nowcast web viewers

Two standalone pages that read **TMD (กรมอุตุนิยมวิทยา)** public radar products,
plus the tiny PHP proxy one of them needs. No build step: serve the folder and
open the HTML.

| file | what it is |
|---|---|
| `tmd_composite.html` | Full-page **MapLibre GL** monitor for the national radar **composite** (PCAPPI at 2 km, `Z = 200·R¹·⁶`). Reads a mirror laid out relative to the page as `./data/`, `./images/` and `./images_composite.list`; falls back to the public geoBoundaries/OpenGISData-Thailand GeoJSON when the local `./data/*.geojson` are absent. Basemap: CARTO positron. |
| `nowcast_index.html` | BKK **nowcast loop** (20 frames) from `satda.tmd.go.th/.../nowcasting/bkk/loop/`. Each pixel is classified against a fixed colour ramp (cloudy → drizzle → light → moderate → heavy → very heavy). |
| `tmd_proxy.php` | The CORS proxy the nowcast page fetches through. Allow-lists `https://satda.tmd.go.th/` **only**, and passes the upstream `Last-Modified` back as `X-Last-Modified` so the page can timestamp a frame. |

## How the two pages get their data

- `nowcast_index.html` never talks to TMD directly. It builds
  `PROXY_URL + encodeURIComponent(<satda url>)` and fetches through
  `tmd_proxy.php` (currently pointed at the deployed copy on
  `gainer.azurewebsites.net`). Change `PROXY_URL` to move it.
- `tmd_composite.html` expects a **mirror of the composite frames** beside it:
  `images_composite.list`, `images/<frame>`, `data/radar.sharing.latest`. It is
  a viewer for that mirror, not a scraper — nothing here downloads the frames.

## Deploy

- Nowcast pair: put `nowcast_index.html` and `tmd_proxy.php` in the same web
  directory and make sure `PROXY_URL` resolves.
- Composite viewer: upload `tmd_composite.html` together with the radar mirror
  (`./data/`, `./images/`, `./images_composite.list`).

> `tmd_proxy.php` turns off TLS peer verification for its one allow-listed
> upstream (TMD's certificate) and reflects the fetched bytes to any caller. It
> is meant for a trusted/thrown-away deployment, not as a general-purpose proxy.
