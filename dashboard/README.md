# headscale web UI — ยกของจาก LXC 106 เข้า version control

หน้าเว็บ headscale (Users / Devices / Groups / Settings) ที่ใช้งานอยู่ที่
<https://arctictong-hs.duckdns.org/web/devices.html>

> เคยเขียนผิดเป็น `arctictonghs` (ไม่มีขีด) มาแล้ว — DNS ของชื่อนั้นไม่มีอยู่จริง
> ชื่อจริงมีขีด ตรงกับชื่อ site ใน nginx (`arctictong-hs`) ดูหลักฐานด้านล่าง

**นี่คือไฟล์ build ที่ถูก copy มา — ไม่ใช่ source code ของ UI**
ไม่มี source ของ SvelteKit อยู่ในมือ (ไม่มี `src/`, ไม่มี `package.json`,
ไม่มี lockfile) มีแต่ผลลัพธ์ `vite build` ที่เอาไปวางบนเซิร์ฟเวอร์ ถ้าจะแก้หน้าตา
ต้องมี source มาก่อน ซึ่ง ticket นี้ไม่ได้ทำ

## Backend คืออะไร — ตอบตรง ๆ: ไม่มี

ไม่มี server-side script ไม่มี PHP ไม่มี Node service ไม่มีอะไรที่รับ request
จาก UI ทั้งหมด nginx บน 106 เป็น **static file server ล้วน** (`listen 9080`,
`root /var/www/headscale-ui`, ไม่มี `proxy_pass` สักตัวใน nginx -T ทั้งชุด)

สิ่งที่เกิดขึ้นจริงเมื่อเปิดหน้านี้:

```txt
เบราว์เซอร์
  ├─ GET /web/... และ /web/_app/...          → NPM (CT 101) → 106:9080 nginx → ไฟล์ static
  └─ fetch(<headscaleURL> + "/api/v1/...", {
        headers: { Authorization: `Bearer ${apiKey}` }
     })                                        → NPM (CT 101) → headscale REST v0.29 บน 106
```

headscale เป็นตัวที่รับ `/api/v1/*` เอง ไม่ใช่ nginx — พิสูจน์จาก response header
(ถ้าเป็น nginx จะได้ header ของ nginx ไม่ใช่ชุดนี้):

```txt
$ curl -i https://arctictong-hs.duckdns.org/api/v1/user
HTTP/1.1 401 Unauthorized
Server: openresty                      ← ตัวหน้าคือ NPM
Content-Security-Policy: frame-ancestors 'none'    ← ชุด header ของ headscale
X-Frame-Options: DENY                             ← (nginx ไม่ได้ใส่มาเอง)
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
Strict-Transport-Security: max-age=63072000; preload

Unauthorized                            ← body
```

401 คือ "เจอปลายทางถูก แค่ไม่มี key" → **NPM มี custom location `/api/` วิ่งต่อไป
headscale บน 106** ดังนั้นไม่ต้องเปิด CORS และไม่ต้องเปิดพอร์ต headscale ออกด้านนอก
(ส่วน `/web/*` วิ่งไปที่ nginx ตามปกติ)

หลักฐานชุดที่สองว่า domain คืออะไร — ชื่อที่ระบุไว้เดิม (`arctictonghs`, ไม่มีขีด)
แปลงชื่อไม่ได้เลย:

```txt
Resolve-DnsName arctictonghs.duckdns.org  → ไม่มี A record
Resolve-DnsName arctictong-hs.duckdns.org → 171.96.83.111
```

หลักฐานชุดที่สามว่าไฟล์ที่เสิร์ฟอยู่คือไฟล์ใน repo นี้จริง ไม่ใช่ของที่ค้างบน 106:

```txt
$ curl -s -o tmp https://arctictong-hs.duckdns.org/web/<ไฟล์> ; sha256sum tmp
→ ตรงกับ dashboard/web/<ไฟล์> ทั้ง 11 ไฟล์ที่เช็ค (html, css, js, png, env.js)
```

หลักฐานจาก bundle ที่อยู่ใน `web/_app/immutable/` — อ่านออกมา ไม่ใช่เดา:

| สิ่งที่เจอ | ไฟล์ | แปลว่า |
|---|---|---|
| `fetch(n+s, {headers:{Authorization:\`Bearer ${e}\`}})` | `chunks/Czdn0cP6.js` | ทุก call ใส่ Bearer token |
| `"/api/v1/node"`, `"/api/v1/user"`, `"/api/v1/apikey/expire"`, `"/api/v1/preauthkey"` | `chunks/Czdn0cP6.js` | endpoint คือ REST ของ headscale ตรง ๆ |
| `localStorage.getItem("headscaleURL")` / `setItem("headscaleURL", e.replace(/\/+$/,""))` | `chunks/*.js` | base URL ตั้งเองโดยผู้ใช้ (ตัด `/` ท้ายออก) |
| `localStorage.getItem("headscaleAPIKey")` | `chunks/*.js` | API key อยู่ในเบราว์เซอร์ |
| `_app/env.js` = `export const env={}` | `web/_app/env.js` | **ไม่มี secret ฝังใน build** |

### Auth เป็นแบบไหน

ผู้ใช้เปิดหน้า **Settings** แล้ววาง headscale URL กับ API key เอง ค่านั้นถูกเก็บใน
`localStorage` ของเบราว์เซอร์นั้น (ไม่มี cookie, ไม่มี session, ไม่มี login ของ UI)
ทุก request จึงพา `Authorization: Bearer <key>` ไปด้วย — key คือ API key ของ headscale
ที่มีอายุ **90 วัน** (ข้อจำกัดของ headscale 0.29)

ผลตามมา: ใครก็ได้ที่เปิดหน้านี้ในเครื่องนั้นและมี key ก็ใช้ key ได้เต็มที่
หน้า Settings มีปุ่มดูวันหมดอายุ/สร้างใหม่ (`/api/v1/apikey/expire`,
`/api/v1/apikey`) — ไม่มี key ฝังใน git จึงไม่มีอะไรต้อง rotate ย้อนหลังจากที่ยกไฟล์มา

### ยืนยันข้อสุดท้ายที่อยู่ฝั่งผู้ใช้เท่านั้น

ค่า `headscaleURL` ที่ตั้งไว้ในเบราว์เซอร์ — ดูได้ด้วย
`localStorage.getItem('headscaleURL')` ใน console ไม่ต้องรีบหาเพราะตัว origin
ข้างบนพิสูจน์แล้วว่าใช้งานได้ (ตอบ 401 = รอ key) การย้ายไป CT 109 จึงต้องทำ
ให้ `/api/` บน NPM ยังชี้ปลายทางถูก ซึ่งเป็นหน้าที่ของ ticket 02

สิ่งที่ **ไม่** ต้องทำแล้ว (ตอนนี้พิสูจน์ได้ว่าไม่จำเป็น): เปิด CORS ใน headscale config
และเปิดพอร์ต headscale ออกทางอินเทอร์เน็ต เพราะ request วิ่ง same-origin ผ่าน NPM

## โครงไฟล์

```txt
dashboard/
  web/                       ← ทุกไฟล์ที่ 106 เสิร์ฟ (27 ไฟล์) copy มาแบ byte-identical
    index.html               ← SvelteKit shell, base: "/web" + CSP (script-src 'self')
    devices.html             ← หน้า Devices (ทางเข้าเดิมที่ใช้อยู่)
    users.html  groups.html  settings.html
    favicon.png
    _app/env.js              ← ว่างเปล่า (SvelteKit public env — จุดที่ "ควร" เก็บค่า config)
    _app/version.json        ← build timestamp ของ SvelteKit
    _app/immutable/…         ← bundle ทั้งหมด (chunks / entry / nodes / assets)
  nginx/headscale-ui.conf    ← server block ที่ 106 ใช้จริง (ถอดจาก nginx -T, ไม่แก้)
  deploy.ps1                 ← ยิง web/ ขึ้น 106
  README.md
```

หนึ่งใน 27 ไฟล์คือ `_app/immutable/assets/_layout.BQaBh7y2.css` ซึ่ง **ไม่ถูกอ้างถึง
จากที่ไหนเลย** (html ไม่อ้าง, js ไม่อ้าง, hash เหมือน `0.BQaBh7y2.css` เป๊ะทุก byte)
คือไฟล์เปล่าที่ค้างมากับ build — ไม่ต้องลบ เพราะ ticket นี้ห้ามแตะ แต่รู้ไว้ว่าถ้าวันหนึ่ง
เห็นมันแล้วคิดว่า CSS ตัวที่ 2 ต้องรันด้วย มันไม่ได้ทำอะไรเลย

`web/**` มี `.gitattributes` กัน CRLF เพราะเครื่องนี้ตั้ง `core.autocrlf=true`
ถ้าไม่กัน git จะเปลี่ยน line ending ตอน checkout ทำให้ไฟล์ใน git ไม่ตรงกับบนเซิร์ฟเวอร์

## nginx

`nginx/headscale-ui.conf` คือ block ที่**ใช้งานจริง** ถอดมาจาก `nginx -T` บน 106
ห้ามแก้ไฟล์นี้เทียบกับต้นฉบับโดยไม่ตั้งใจ

พบ config ตกหล่น: `/etc/nginx/sites-available/arctictong-hs` มีอยู่แต่**ไม่ได้
symlink เข้า `sites-enabled`** → nginx ไม่ได้อ่าน ไม่มีผลกับการให้บริการ
(ค้นหาไฟล์นี้แล้วจะเข้าใจว่าทำไม script ตอนแรกเดา docroot ถูก แต่ไซต์ไม่ใช่ตัวที่ทำงาน)

## Deploy

**ก่อนหน้านี้ deploy = มือ** (copy ไฟล์ขึ้น 106 ตอนสร้าง ไม่มีใน git ไม่มีสคริปต์
ถ้าไฟล์หายกู้จากที่ไหนไม่ได้) — จาก commit นี้เป็นต้นไปมี `deploy.ps1`

```powershell
powershell -ExecutionPolicy Bypass -File F:\OpenCode\dashboard\deploy.ps1
```

- รวม `web/` เป็น tarball แล้ว scp ไป `/tmp` → แตกทับ `/var/www/headscale-ui/`
  → `chmod -R u=rwX,go=rX` (nginx รันเป็น www-data)
- **ยิงทุกไฟล์ทุกครั้ง ไม่ทำ diff** เพราะเกณฑ์ทดสอบคือ "ลบ css บนเซิร์ฟเวอร์
  แล้วรันสคริปต์ หน้าต้องกลับมาครบ" ถ้าทำ diff ไฟล์ที่ถูกลบจะไม่มีวันกลับมา
- พอกลับมา สคริปต์จะพ่นจำนวนไฟล์บนเซิร์ฟเวอร์มาเทียบกับในเครื่อง
- ใช้ `$PSScriptRoot` ไม่ hardcode path (`tsguard/deploy.ps1` ยังจม `E:\OpenCode`
  ตั้งแต่ย้ายไป `F:` มา — เจอเรื่องนี้ตอนอ่านโค้ด)
- พอร์ตและ path เป็น parameter → ตอนย้ายไป CT 109 ใช้สคริปต์เดิมได้
  (`-TargetHost`, `-RemoteRoot`)
- ไม่แตะ nginx เลย: ไฟล์ static ไม่ต้อง reload

**deploy ถูกรันจริงกับ 106 แล้ว** — ทดสอบโดยลบ `0.BQaBh7y2.css` (ไฟล์ที่ทุกหน้า
`<link>` อ้างถึง) ออกจากเซิร์ฟเวอร์ แล้วรันสคริปต์: ไฟล์กลับมาครบ 27 ตัว พร้อม
mtime เดิม และดึงจาก URL สาธารณะมาเทียบ sha256 ตรงกับใน repo ทั้ง 11 ไฟล์ที่เช็ค

## ข้อควรระวัง

- **ห้ามแก้ไฟล์ใน `web/`** นอกจากเป็น build ใหม่จาก source ที่ยังไม่มี — แก้แล้ว
  deploy ครั้งหน้าจะทับกลับ และไม่มี source ให้ build ใหม่
- API key อยู่ฝั่งเบราว์เซอร์ ไม่ได้อยู่ฝั่งนี้ → การหน้าเว็บเปิดสาธารณะผ่าน NPM
  จะทำให้ใครก็ได้เห็นหน้า Settings แต่ยังใช้ไม่ได้ถ้าไม่มี key (ยังดีกว่าฝัง key ใน build
  เพราะ build ถูก commit ลง git แล้ว)
- CSP ที่ `index.html` มีคือ `script-src 'self' 'sha256-...'` — ถ้าวันหนึ่งจะเพิ่ม
  analytics หรือ CDN ต้องแก้ CSP ในไฟล์ build ตรงนี้ ซึ่งเป็นอีกเหตุผลว่าควรมี source
- `web/_app/immutable/` มีชื่อไฟล์ผสม hash (`nodes/3.Icl2lbju.js`) — ถ้าสับสนว่ามันคืออะไร
  ใหนมองจาก `index.html` แล้วไล่ `import(...)` ใน `entry/app.*.js`

## Ops dashboard (tickets 03–05)

นอกจาก static UI แล้ว repo นี้มีแอป Python เล็ก ๆ ที่เสิร์ฟหน้า `/dashboard/` รวมสถานะ homelab
(`dashboard/src/dashboard/`):

| endpoint | ข้อมูล |
|---|---|
| `GET /api/health` | liveness + จำนวน node/service ที่ตั้งค่าไว้ |
| `GET /api/nodes` | node จาก headscale REST (`display_name` เรียง `givenName` → `name` → `user.displayName` → `id`) |
| `GET /api/services` | probe รายบริการ (`http`/`tcp`) + latency จริง |
| `GET /api/pve/summary` | guest บน Proxmox + task ที่ fail + backup ล่าสุด |

แอป bind `127.0.0.1:8001` เท่านั้น — nginx บน CT 109 (`nginx/dashboard`) เป็นคนเปิดให้ `/api/`
กับ `/dashboard/` · ตอบ **JSON เสมอแม้ upstream ตาย** ไม่มี HTML 500 หลุดออกไป
· หน้าเว็บแค่ `location.reload()` ทำให้ไม่มีปัญหา same-origin กับ public host

### PVE token เป็น read-only

```txt
user      dashboard@pve        role PVEAuditor
token     dashboard@pve!dashboard              (pveum user token add ... --privsep 0)
value     /etc/dashboard/secrets/pve.token     (mode 600)
PVE_URL   https://192.168.1.190:8006           node name = pve
```

`PVE_TOKEN_ID` อยู่ใน config **ไม่ใช่ในไฟล์ secret** เพราะ token id ไม่ใช่ความลับ
(ไฟล์ secret เก็บแค่ value) — secret จึงไม่โผล่ใน `ps`/log/config

พิสูจน์ว่า read-only จริง:

```txt
POST /api2/json/nodes/pve/lxc/999/status/start
  -> 403 Permission check failed (/vms/999, VM.PowerMgmt)
```

`PVE_VERIFY_TLS=false` เฉพาะใน unit บน 109 (cert ของ PVE เป็น self-signed) —
**default ในโค้ดยังเป็น `true`** ไม่ให้ความสะดวกของ homelab กลายเป็นค่า default ของโค้ด
(pin cert = งานรอบ 2)

### กับดักของ PVE API ที่เจอจริงกับเครื่องนี้

- `cluster/tasks` **ไม่รับ parameter ใด ๆ** — `limit`/`type`/`source` → `400 property is not defined in schema`
- ความสำเร็จดูจาก `status == "OK"` เท่านั้น — task ที่ fail เอา error text ใส่ `status` ตรง ๆ
  · task ที่ยังรันไม่มีทั้ง `status` และ `endtime`
- **`uptime` เป็น `0` ให้ guest ที่ stopped** ไม่ใช่ "ไม่ส่ง key" — โมเดลจึงทิ้ง uptime ทุกสถานะ
  ที่ไม่ใช่ `running` แล้วหน้าเว็บพิมพ์ `—` ไม่ใช่ `0s`
- เครื่องนี้ **ไม่มี backup job** (`GET /cluster/backup` = `[]`) → `backup_recent` เป็น false
  ทั้ง 10 ตัว **เป็นคำตอบที่ถูก** · หน้าเว็บขึ้นแถบเหลืองอธิบาย ไม่ปล่อยเป็นจุดแดง 10 จุดลอย ๆ
  · `backup_jobs` ใส่ใน response เฉพาะเมื่อถาม PVE สำเร็จ (ไม่ใส่ = "ไม่รู้" ไม่ใช่ "ไม่มี")

## ต่อไป

- **ticket 08 — เข้าถึงจากข้างนอกได้ (ทำแล้ว):** เพิ่ม **Custom Location `/dashboard/` →
  `192.168.1.200:9080`** แถวเดียวใน NPM `proxy_host.id = 12` โดย **คง ACL เดิม** (LAN + Tailscale)
  → `https://arctictong-hs.duckdns.org/dashboard/` = `200` + `X-Served-By: ct109`
  · **ไม่ได้เปิด `/api/`** (ยัง `401` เหมือนเดิม) เพราะหน้าเว็บ render ฝั่งเซิร์ฟเวอร์ล้วน (ไม่มี `fetch`)
  และ `/api/*` เป็นของ headscale UI — เปิดเมื่อไหร่ UI เดิมพังทันที
- **`X-Served-By: ct109`** = header ที่ nginx บน 109 ใส่ให้ทุก response ใช้ตัดสินได้เสมอว่า
  request ถึง 109 จริง หรือถูก headscale ตอบไปแล้ว
- **ticket 06** (tsguard handlers) implement เสร็จแล้วแต่**ยังไม่ deploy** — ดู ticket ใน board
- เรื่องที่เจ้าของต้องตัดสินเอง: เครื่องนี้ไม่มี backup job เลย (`/cluster/backup` = `[]`)
  → หน้าเว็บขึ้นแถบเหลืองเตือนทุกครั้ง · การสร้าง job เป็นงานของเจ้าของ ไม่ใช่ของ dashboard
