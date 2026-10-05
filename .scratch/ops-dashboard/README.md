# Ops Dashboard + tsguard Command Center — Board

Spec: `C:\Users\elib_tong\.opencode\plan\ops-dashboard-tsguard-spec.md`
Sliced: 2026-09-30 · เพิ่ม 09 เมื่อ 2026-10-02 · local tracker (`.scratch/ops-dashboard/issues/`, blockers-first)

## ลำดับการทำงาน (ตาม blocking edges)

```txt
01 ──► 02 ──► 03 ──┬──► 04 ──► 05 ──► 08 (optional)
06 (ขนานได้)       └──► 07 (optional)
09 (ขนานได้ — เขียนตามสเปกบรรทัด 139 ที่ตกหล่นจนถึง 2026-10-02)
```

## Frontier ปัจจุบัน

| Ticket | สถานะ | เริ่มได้เมื่อ |
|---|---|---|
| 01 | ☑ เสร็จ (`607f857`–`20ee64b`) | — ครบทั้ง 3 AC, deploy ทดสอบกับ 106 จริงแล้ว |
| 02 | ☑ เสร็จ (`75854fa`) | — cutover ครบ, static ถอนจาก 106 แล้ว, ACL + API key บน 109 |
| 03 | ☑ เสร็จ (`9ac457e`) | — deploy บน 109 แล้ว, `/api/nodes` 17/17 ตรงกับ `tailscale status --json` |
| 04 | ☑ เสร็จ (`e8c9128`) | — 4 service เขียวครบพร้อม latency จริง, พิสูจน์ `pct stop 105` แล้ว |
| 05 | ☑ เสร็จ (`790306c`) | — deploy จริงบน 109, guests 10/10 ตรง `cluster/resources`, token read-only (403), พิสูจน์ `pct stop 105` แล้ว |
| 06 | 🟡 deploy แล้ว (`a9f55e8` บน `tsguard/main`) — **`/ping` ใช้ได้จริง**, `/restart` ยังไม่เปิดตามที่เจ้าของเลือก | — deploy 2 รอบ (`deploy.ps1`), เจอบั๊ก argv ขาด subcommand `ping` ตอนทดสอบบนเครื่องจริง · wrapper `tsguard-restart` เขียนแล้วแต่ยังไม่ติดตั้งที่ไหน และ alias ไม่ถูกใส่ใน config จริง (เจ้าของเลือก "ยังไม่ใส่ targets") |
| 07 | ⊘ ตัดออก (2026-10-01) | ดูเหตุผลในไฟล์ ticket — node key หมดอายุจะยังเงียบ · เหลือแต่ note rotate API key → orphan |
| 08 | ☑ ปิดแล้ว (2026-10-02) — **ย้อนกลับ/ไม่ทำต่อ** (NPM Custom Location — ไม่มี commit) | — `/dashboard/` ถูกถอดออกจาก public แล้ว ⇒ กลับเป็น `404` · `/web/` `200` · `/api/nodes` `401` · LAN `200` · เจ้าของเลือก "ดูแค่ภายใน" · สาเหตุที่ต้องถอด: `192.168.1.198` = **hubrelay CT 107** + router **forward 443 → hubrelay** ⇒ ACL กัน 443 ไม่ได้ (ดู fact ACL + orphan) |
| 09 | ⬜ ยังไม่เริ่ม (เขียน ticket แล้ว 2026-10-02) | — ขนานได้เลย · tsguard poll `GET /api/health` ของ 109 แล้วแจ้งเมื่อ dashboard ตาย · **ฝั่ง dashboard รออยู่แล้ว** ที่ `dashboard/src/dashboard/web/app.py:162` — เหลือแค่คนมาอ่าน |

## ข้อเท็จจริงที่ได้มาระหว่างทาง (อย่าต้องค้นซ้ำ)

- **ของที่สเปกสั่งไว้ มีอยู่แล้วเกือบทั้งหมด — อย่าไปตั้งใจทำซ้ำ** (เช็ค 2026-10-02 ก่อนเสนอ ticket):
  - **daily digest มีแล้ว** — `tsguard/src/tsguard/app.py:622 _maybe_digest()` ยิงจาก
    `maintenance_loop` ที่ tick ทุก 60 วินาที ไม่ใช่ cron · ตั้งเวลาได้ที่ `notifier.digest`
    (`daily: "08:00"`, `weekly: "Mon 08:30"`) — **default คือ 08:00 ไม่ใช่ 06:00 ตามสเปก**
  - **เตือน key ใกล้หมดอายุมีแล้ว** — `evaluator.expiring_soon()` เตือนก่อน
    `expiry_warn_early` (7 วัน) และ `expiry_warn_late` (1 วัน) — อ่านจาก headscale ตรง ไม่ต้องผ่าน 109
  - **`/api/health` บน 109 มีแล้ว** — `dashboard/src/dashboard/web/app.py:162` คืน `status: ok`
    และ**ไม่แตะ upstream เลย** (เป็น liveness ของตัวแอป) — docstring เขียนรอไว้ว่า
    *"this is what tsguard polls"* แต่ tsguard ยังไม่มีโค้ด poll → ticket 09
- **app ของ dashboard ผูก `127.0.0.1:8001` loopback** (ดู comment ใน `dashboard/nginx/dashboard`)
  ⇒ tsguard ต่อ `8001` ตรงไม่ได้ ต้องผ่าน nginx บน 109 ที่ `:9080` และ**ไม่ต้อง credential**
  (basic auth อยู่ที่ NPM ไม่ใช่ที่ 109, `nginx/dashboard` ไม่มี `auth_basic`)
- **`/status` ไม่ตรงสเปก — ตัดสินไม่ทำแก้** — สเปกบรรทัด 91 สั่งให้เรียก Dashboard API แล้วจัดกลุ่ม
  myhome/tagged-devices แต่สเปกบรรทัด 98 บอกเองว่า source of truth คือ `lastSeen` จาก headscale
  ซึ่ง tsguard **อ่านตรงอยู่แล้ว** (`bot.py:724 _status` อ่าน store ตัวเอง) ⇒ การวิ่งออกไป 109 เพื่อถาม
  headscale แล้วกลับมาเป็นรอบที่ไม่มีเหตุผล และทำให้ `/status` พังตาม 109 ทั้งที่ข้อมูลอยู่ในมือแล้ว
- **`tsguard` push แล้ว 2026-10-02** — `c7c502e..462ee7e` ขึ้น `github.com/arctictong/tsguard`
  (`HSBridge/` ถูก ignore — มี WireGuard private key อยู่ในนั้น, `.gitignore` บรรทัด 28)
- **⚠️ `F:\OpenCode` (repo `master`) ไม่มี remote เลย** — board, สเปก, และ **โค้ด dashboard ทั้งหมด**
  อยู่ในนี้ (`dashboard/` ไม่ใช่ repo แยก) มีสำเนาเดียวบนดิสก์เครื่องนี้ — ยังไม่ตัดสินว่าจะทำอย่างไร

- **tsguard (CT 108) คือ `192.168.1.199` / `100.64.0.16`** · venv = Python **3.13** · install แบบ
  **editable** (`_editable_impl_tsguard.pth` → `/opt/tsguard/src/tsguard`) ⇒ deploy = แตก tarball ทับ
  `/opt/tsguard` แล้ว restart **ไม่ต้อง `pip install`** (และ venv นี้ไม่มี `pip` ด้วย) · secret อยู่ที่
  `/etc/tsguard/.env` (default ของ `tsguard` คือ `--env-file /etc/tsguard/.env`) — ระวัง `ls -l` ซ่อนไฟล์จุด ต้อง `ls -la`
- **`deploy.ps1` เคย hardcode `E:\OpenCode\tsguard`** ซึ่งไม่มีแล้ว และ fail แบบเงียบ เพราะ `Set-Location`
  ทำงานไปแล้ว `tar` เลยแพ็คไดเรกทอรีที่มันอยู่ → **deploy ที่ผ่านมาก่อนหน้านี้อาจไม่ได้ deploy อะไรเลย**
  ยืนยันแล้วว่า 108 ค้างที่โค้ดก่อน ticket 06 ทั้งที่เคย push tarball ไปแล้ว (`local` field ไม่มีในของที่ติดตั้ง)
- **`local: true` แปลว่า argv = `[wrapper, *args]` ไม่ใช่ `[wrapper, subcommand, *args]`** ⇒ subcommand
  ของ tailscale ต้องเป็น arg ที่ประกาศใน `args_allowed` ด้วย (`ping`) ไม่งั้น cobra อ่าน `--c=4`
  เป็น global flag แล้วพ่น usage ทั้งหน้าออกมาหน้า exit 2 · **เทสต์ที่ assert argv จับไม่ได้**
  เพราะมัน assert ว่าโค้ดสร้าง argv ตามที่โค้ดสร้าง — ต้อง assert *โครงร่าง* แยก
- **`tailscaled` คือ Tailscale SSH server ของเครื่องนั้นเอง** ⇒ สั่ง `systemctl restart tailscaled`
  ตรง ๆ ผ่าน `tailscale ssh` จะตัด channel ที่ tsguard รอ exit code อยู่ ทำให้รายงานว่าล้มเหลวทั้งที่สำเร็จ
  · wrapper `/restart` จึง schedule ผ่าน `systemd-run --on-active` แล้วค่อยรายงานว่า "scheduled"
  (ตัวเดียวกับเคส `reboot`)
- **alias `uptime` พังจริง** — ชี้ `/usr/local/bin/tsguard-remote-command.sh` ซึ่งมีแค่บน 108 ไม่มีบน hubrelay
  ⇒ `/run uptime <node>` ได้ exit 127 ทุก node · auto-remediate กับ wireguard watch ไม่กระทบ (ใช้ wrapper ของตัวเองที่ครบ)
- **auto-remediate ของ tsguard ไม่พัง** — อาวุธไว้สองทาง (zombie + wg peer stale) ทั้งคู่ชี้ `tsguard-restart-wg.sh`
  บน hubrelay และ**มี wrapper ครบจริง** พิสูจน์จาก log ว่าเคยรันสำเร็จแล้ว (`skipping … cooldown left`)
  · zombie ที่โผล่ (`pchome`, `tong-nb`) ถูก allowlist ปฏิเสธถูกต้องเพราะอยู่นอก `targets` = พฤติกรรมที่ออกแบบไว้
- **unit ของ tsguard ตั้ง `NoNewPrivileges=yes` + `CapabilityBoundingSet=` (ว่าง)** ⇒ daemon ยกเป็น root ไม่ได้
  ดังนั้น `/etc/sudoers.d/tsguard-remote` ที่ให้ user `tsguard` รัน wrapper เป็น root ได้ทุก argument
  **ไม่มีทางถูกใช้จาก tsguard** และไม่จำเป็น (remote `sudo -n` ทำงานปลายทางเป็น root อยู่แล้ว, local ไม่ใช้ sudo)
  — เป็นสิทธิ์ค้างที่ควรถอน แต่ไม่เร่งด่วน
- **tsguard มี dashboard ของตัวเองอยู่แล้ว** ที่ `https://100.64.0.16:8443` (basic auth จาก `/etc/tsguard/htpasswd`)
- **headscale API key หมดอายุ 90 วัน** และตอนหมดอายุ headscale ตอบ 401 ซึ่งโค้ดทำเป็น **non-retryable**
  และแจ้งเตือน (`notifier.alert_on_headscale_api_error` default `true`) — สิ่งที่หายเงียบคือ
  **cross-check → ตัวตรวจ zombie ตาบอด** ส่วน tailscale status กับ wireguard watch ยังทำงาน
  → runbook อยู่ที่ `tsguard/docs/rotate-headscale-key.md` (เขียนแล้ว 2026-10-02, ยังไม่ได้หมุนจริง)

- URL จริงของ UI = `https://arctictong-hs.duckdns.org/web/devices.html` (**มีขีด** —
  ชื่อที่เคยเขียนไว้แบบไม่มีขีดไม่มี DNS record)
- **NPM (CT 101) route `/api/` ไป headscale บน 106** พิสูจน์จาก `curl -i
  /api/v1/user` → `401 Unauthorized` + headscale security header → same-origin,
  ไม่ต้องเปิด CORS · ตอนย้าย UI ไป 109 ต้องคง route นี้ไว้
- `/web/*` → 109:9080 nginx (static ล้วน) — ย้าวเมื่อ 2026-10-01 (ticket 02) — ยืนยันด้วย header `X-Served-By: ct109` และ sha256 27/27 ไฟล์
- **CT 106 เหลือ headscale ล้วนแล้ว** — nginx `stop`+`disable`, docroot ถูกลบ
  (สำรองเป็น tar.gz ไว้บน PVE host ที่ `/root/106-ui-backup-*.tar.gz`)
- **ACL ระดับ host ที่ NPM (`proxy_host.id=12`)** = `allow 192.168.1.0/24;`
  `allow 100.64.0.0/10;` `allow 10.8.0.0/24;` `deny all;` — **`100.64.0.0/10` ห้ามตัดออกเด็ดขาด**
  เพราะ Tailscale client ทุกตัว bootstrap เข้าผ่าน `server_url` (ชนกับ CGNAT ของมือถือ แต่แก้ไม่ได้)
  · ใส่ใน **Custom Nginx Configuration ระดับ host** ไม่ใช่แท็บ Custom Locations
  · ❌ **2026-10-02: สาเหตุยืนยันแล้ว — ACL กัน 443 ไม่ได้** · `192.168.1.198` = **hubrelay (CT 107)**
  · router forward **external 443 → 192.168.1.198** ⇒ NPM เห็น external HTTPS ทุกครั้งเป็น `.198`
  ซึ่งอยู่ใน `allow 192.168.1.0/24` ⇒ `deny all;` ไม่มีโอกาสทำงาน · `grep -c ' 403 403 '` = **0**
  · public IP จริงที่โผล่ใน log ล้วนเป็น traffic **port 80** (`POST /ts2021` ของ Tailscale) ได้ `301`
  (scheme: `http` 100 · `https` 96,505) · log format = `[Client $remote_addr]` (`conf.d/include/log-proxy.conf`)
  · ตรวจแล้วว่า ACL อยู่ครบทั้งในไฟล์/`nginx -T` (1120/1123, 1126/1129)/DB(7) → **config ไม่ผิด**
  ⇒ **ACL ที่อิง `$remote_addr` ใช้ไม่ได้กับ host นี้** เพราะ ingress ถูก relay หมด — ไม่ใช่เรื่อง config
  ⇒ ต้องใช้ auth · **กระทบ `/web/` + `/api/*` ตั้งแต่ ticket 02** (รอดเพราะ headscale มี auth เอง)
  · ⚠️ **ห้ามแก้ด้วยการ bypass hubrelay ให้ 443 ตรงไป NPM** — Tailscale client นอกบ้าน
  (`/ts2021`, `/derp`) มาจาก public IP ของ carrier → จะโดน `deny all` → **tailnet ล่ม**
- **ห้ามแก้ Forward Hostname/IP ของ host หลักใน NPM** — ค่านั้นคือทาง `/api/` ถ้าพลาด
  หน้าเว็บยังเปิดได้แต่ทุกปุ่มจะพังเป็น 401 · แก้เฉพาะแถว Custom Location `/web/` เท่านั้น
- **API key ของ 109 หมดอายุ 2026-12-30** — `/etc/dashboard/secrets/headscale.key` (mode 600,
  โฟลเดอร์ `secrets` mode 700) · headscale 0.29.3 `apikeys create` **ไม่มี flag `--user`
  ต่างจากที่เดา** = API key เป็นระดับเซิร์ฟเวอร์ ไม่ผูก user
- **CT 109**: Debian 13, 1 core / 1024 MB, `192.168.1.200`, nginx 1.26.3,
  docroot `/var/www/headscale-ui`, `onboot: 1`
- **`status.<domain>` ทำซ้อนไม่ได้** — duckdns ให้ subdomain ชั้นเดียว → path ที่ตั้งใจไว้สำหรับหน้ารวม
  dashboard คือ **`/dashboard/`** บน host เดิม (`arctictong-hs.duckdns.org`) ไม่ใช่ `/` เพราะ `/` บน
  public host คือทางไป headscale เอง · บน 109 `location = /` ตอบ `302 → /web/` (docroot มีแต่ `web/`
  ไม่มี `index.html` ที่ราก เดิมจึงตอบ 403)
  ⚠️ ~~**แต่ NPM ยังไม่ได้เปิด `/dashboard/` ออก public** — วันนี้เข้าถึงได้แค่ LAN
  (`http://192.168.1.200:9080/dashboard/` → `200`) ส่วนบน public ตอบ `404 page not found`~~
  → ✅ **2026-10-01 เปิด → 2026-10-02 ถอดออก:** เพิ่ม Custom Location เพื่อให้เข้าจากข้างนอกได้
  แต่พบว่า **ACL กัน 443 ไม่ได้** (router ส่ง 443 ผ่าน hubrelay `.198`) ⇒ เจ้าของเลือก **ดูแค่ภายใน**
  → **ลบ Custom Location ออกแล้ว** `/dashboard/` กลับเป็น `404` ให้ทุกคน · ในบ้านยัง `200` ที่
  `http://192.168.1.200:9080/dashboard/` · ดู fact ACL + ticket 08
- **log ของ NPM ไม่ได้ bind-mount** — `/data/logs/...` เป็นพาธใน container
  ต้องอ่านผ่าน `docker exec nginx-proxy-manager-app-1` เท่านั้น
  · `[Sent-to ...]` **ไม่ใช่ upstream ของ location** — มันคือ `$server` ระดับ host (main forward)
  บรรทัด `/dashboard/` จึงขึ้น `[Sent-to 192.168.1.197]` (headscale) ทั้งที่ของจริง 109 ตอบ
  → **อย่าใช้ `[Sent-to]` ตัดสิน routing ให้ใช้ `X-Served-By: ct109`**
  · `[Length nnnn]` = ไบต์บนสาย (หลัง gzip) · nginx บน 109 gzip `/dashboard/` อยู่ (default ของ Debian,
  `Content-Encoding: gzip` จริง) → `[Length 2622]` = หน้าจริง ~11 KB ที่ถูกบีบ ไม่ใช่หน้าถูกตัด
  · อย่าเทียบ `[Length]` กับ `Content-Length` ของ curl (curl ไม่ส่ง `Accept-Encoding` จึงได้ 11229 ตัวเต็ม)
- **จะรู้ว่า "เปิดออก internet จริงไหม" ต้องยิงจากข้างนอกจริง** — `webfetch` ของ agent รันอยู่ **ใน LAN**
  (พิสูจน์: ยิง `http://192.168.1.200:9080/dashboard/` ตรง ๆ สำเร็จ ทั้งที่เป็นที่อยู่ LAN ล้วน)
  → ใช้ตัดสินเรื่อง ACL ไม่ได้เลย · วิธีที่ไม่รั่วเนื้อหา:
  `https://api.hackertarget.com/httpheaders/?q=<url-encoded>` (คืนแค่ **header ไม่คืน body**)
  ⚠️ **แต่แม้ hackertarget ก็ยังไม่สะอาด** — probe เข้ามาเป็น `[Client 192.168.1.198]` ไม่ใช่ public IP
  ของ hackertarget ⇒ อ่าน `403`/`404`/`401`/`200` จากบริการนี้ **เชื่อไม่ได้ 100%** ·
  ตัวชี้ขาดจริง ๆ คือ **จำนวน `403` ใน log ของ NPM** ไม่ใช่ผลจากบริการภายนอก
- **`.ps1` ที่มีอักษรไทยต้องมี UTF-8 BOM** — PS 5.1 อ่านไม่มี BOM เป็น cp1252
  อักษรไทยกลายเป็นเครื่องหมาย quote ตัวโกง → syntax error ทั้งที่โครงสร้างถูก
  **ส่วน `.sh` ตรงข้าม: ไม่มี BOM + เป็น LF**
- **NPM (CT 101) มี proxy host แค่ 3 ตัว ไม่ใช่ 7** — `2.conf` = `arctictong-ha.duckdns.org` →
  `192.168.1.248:8123` (HomeAssistant) · `3.conf` = `arctictong-notes.duckdns.org` →
  `192.168.1.196:8090` (NoteApp) · `12.conf` = `arctictong-hs.duckdns.org`
  ⚠️ **"→ `192.168.1.200:9080`" ที่จดไว้เดิมไม่ครบ** — พฤติกรรมที่วัดได้จริง: `/web/` → 109 (`200`)
  แต่ `/api/*` → headscale (`401 Unauthorized` text/plain) และ `/dashboard/` → headscale
  (`404 page not found`) → ตรงกับ fact "main forward = headscale + Custom Location `/web/` → 109"
  · ยังไม่ได้อ่าน `12.conf` ตรง ๆ (จะได้เห็น `location` ที่ NPM generate เอง — ใช้ยืนยันแบบไม่ต้องเดา)
  `docker exec nginx-proxy-manager-app-1 grep -rnE 'server_name|location|proxy_pass' /data/nginx/proxy_host/12.conf`
  · ตัวอื่น (1–7, 8–11) ไม่มี proxy host · กวาดทั้งชุดด้วย
  `docker exec nginx-proxy-manager-app-1 grep -rnE 'server_name|proxy_pass' /data/nginx/proxy_host/`
- **hairpin NAT ใช้ได้** — จาก 109 ยิง `https://arctictong-ha.duckdns.org` ได้ `200` ใน ~280ms
  จึงพร่องผ่าน NPM จริง (DNS → router → NPM → app) ได้ ไม่ต้องยิง IP ตรง
- **ป้ายชื่อ node ใช้ `givenName` ไม่ใช่ `name`** — ของจริง `name="DESKTOP-CDFJ4TV"` แต่
  `givenName="pchome"` และ spec ของ ticket 03 ก็เขียน `"name": "pchome"` ไว้แบบนั้น
- **RustDesk ไม่มี HTTP** (21115/21116/21117 เป็น TCP ล้วน) · **hubrelay status** ที่
  `192.168.1.198:8686` ตอบ `401` เพราะบังคับ Bearer token → ทั้งคู่พร่องด้วย `type: tcp`
  (TCP connect) ใน `services.yaml` ไม่ใช่ URL
- `/etc/dashboard/services.yaml` **ไม่ได้อยู่ใน git** (แบบเดียวกับ `secrets/headscale.key`) —
  deploy ไม่ทับ แต่ถ้า CT 109 ถูกสร้างใหม่ต้องเขียนใหม่ · สำเนาที่ intent ตรงกันอยู่ที่
  `dashboard/config/services.example.yaml` · แก้ไฟล์แล้วไม่ต้อง restart (แอปอ่านทุก request)

### PVE (ได้มาตอน ticket 05)

- **PVE = `192.168.1.190:8006`, node name = `pve`** (ticket 05 เขียน `192.168.5.x` ไว้ตอนแรก — ผิด)
  · cert self-signed → unit บน 109 ตั้ง `PVE_VERIFY_TLS=false` แต่ **default ในโค้ดเป็น `true`**
  (pin cert = งานรอบ 2)
- **Token ของ PVE เป็น read-only จริง**: user `dashboard@pve` + role `PVEAuditor` +
  token `dashboard@pve!dashboard` (`pveum user token add ... --privsep 0`)
  · เก็บ **value** ที่ `/etc/dashboard/secrets/pve.token` (mode 600)
  · `PVE_TOKEN_ID` อยู่ใน config เพราะ token id ไม่ใช่ความลับ — ไฟล์ secret เก็บแค่ value
  · พิสูจน์: `POST /api2/json/nodes/pve/lxc/999/status/start` → `403 Permission check failed (/vms/999, VM.PowerMgmt)`
- **guest ทั้งหมด 10 ตัว (100–109)**: `100 HomeAssistant` **qemu** · `101 NginxProxyManager` ·
  `102 Rustdesk-server` · `103 Nextcloud` **qemu** · `104 WebServer` · `105 NoteApp` ·
  `106 Headscale-Server` · `107 Hubrelay` · `108 TsGuard` · `109 Dashboard`
  ⚠️ ticket 02 เคยเขียนเลอะว่า "108 HomeAssistant" — **ผิด**: HomeAssistant คือ **VM 100** (และ 108 มีตัวเดียวคือ TsGuard)
- **PVE ไม่มี backup job เลย** — `GET /cluster/backup` = `[]` และใน 25 task ล่าสุดไม่มี `vzdump` สักตัว
  → `backup_recent` = `false` ทั้ง 10 ตัว **นี่คือคำตอบที่ถูก ไม่ใช่บั๊ก** · ห้ามสร้าง job แทนเจ้าของ
  · หน้าเว็บขึ้นแถบเหลืองอธิบาย ไม่ปล่อยเป็นจุดแดง 10 จุดลอย ๆ → ต้องย้ายไป task อื่นรายวัน
- **`cluster/tasks` บน PVE ตัวนี้ไม่รับ parameter ใด ๆ** — `limit`/`type`/`source` ล้วน 400
  `property is not defined in schema` → ดึงมาแล้วกรองเอง
- **`status == "OK"` เท่านั้นคือสำเร็จ** — task ที่ fail เอา error text ใส่ `status` ตรง ๆ
  (เช่น `"failed to open ... for reading"`) · task ที่ยังรันไม่มีทั้ง `status` และ `endtime`
- **`cluster/resources` = list รวมทุกอย่าง** (guest + node + storage = 14 แถว) — guest คือแถวที่
  `type` เป็น `lxc`/`qemu` · PVE ส่ง `type` ดิบ เราแปลง `qemu` → `VM` ที่ชั้น template
- **PVE ส่ง `"uptime": 0` ให้ guest ที่หยุดอยู่ — ไม่ใช่ "ไม่มี key"** (เข้าใจผิดรอบแรกแล้วเทสผ่าน
  เพราะ fixture มีแต่ guest ที่รันอยู่ → หน้าจอโชว์ `0s` ให้ CT ที่หยุด)
  → ตอนนี้ `uptime` เป็น `None` ทุกสถานะที่ไม่ใช่ `running` หน้าเว็บพิมพ์ `—` ไม่ใช่ `0s` (`790306c`)
  · **บทเรียนซ้ำกับ ticket 03: เทสต้องป้อน payload ที่เครื่องส่งจริง**
- **`/dashboard/` เปิดให้เข้าถึงจาก public host ได้แล้ว (ticket 08)** — เพิ่ม **Custom Location แถวเดียว**
  `/dashboard/` → `http://192.168.1.200:9080` ใน NPM `proxy_host.id = 12`
  · main forward / `/api/` / `/web/` / certificate ไม่ถูกแตะเลย
  · ⏳ **แต่ "ACL เดิมยังเป็นกำแพง" ยังพิสูจน์ไม่ได้** — รอบทดสอบแรกผิดวิธี (probe เข้ามาเป็น `.198`
  ซึ่งอยู่ใน allow-list) ⇒ **ยังไม่รู้ว่าเปิดให้ internet อ่านได้จริงหรือไม่** · ต้องเช็คจำนวน `403`
  ใน log ของ NPM ก่อน — ต้องได้หลักฐานก่อนปิด ticket
- **`X-Served-By: ct109` = วิธีตัดสินว่าใครตอบ** — nginx บน 109 ใส่ header นี้ให้ทุก response
  ใช้แยก "ถึง 109 จริง" ออกจาก "headscale ตอบ" ได้ตลอด · สถานะหลัง ticket 08:
  `/dashboard/` → `200` + `X-Served-By: ct109` · `/web/` → `200` ·
  `/api/nodes` → `401` · `/api/v1/user` → `401` (**`/api/` ยังเป็นของ headscale เหมือนเดิม**)
  · ก่อนหน้านี้ `/dashboard/` → `404 page not found` (text/plain 19 bytes = ข้อความ default ของ Go)
  เพราะตกไป main forward (headscale) — **ไม่ใช่ regression** `/api/nodes` ก็ 401 มาตั้งแต่ ticket 03
- **`/api/` เปิดออก public ไม่ได้** — ทั้ง dashboard (`/api/nodes`, `/api/services`, `/api/pve/summary`,
  `/api/health`) และ headscale (`/api/v1/*`) ใช้ prefix เดียวกัน · การเพิ่ม Custom Location
  `/api/` เปล่า ๆ จะ **ฆ่า headscale UI ทันที** (ทุกปุ่มเป็น 401)
  · โชคดีที่ **ไม่จำเป็นเลย**: หน้า `/dashboard/` เป็น server-rendered ล้วน (ไม่มี `fetch`,
  ไม่มี `<link>`, ไม่มี `<script src>` — refresh ด้วย `location.reload()`) → เปิดแค่ path เดียวก็ครบ

## งานที่ยังค้าง (orphan — ยังไม่มี ticket)

- **🔴 ACL ที่ NPM กัน 443 ไม่ได้ → `/web/` + `/api/*` เปิดจาก internet** (ยืนยัน 2026-10-02)
  · `192.168.1.198` = **hubrelay (CT 107)** และ router **forward external 443 → .198** ⇒ NPM เห็น
  external HTTPS ทุกครั้งเป็น `.198` (อยู่ใน `allow 192.168.1.0/24`) ⇒ `deny all;` ไม่เคยทำงาน
  · `grep -c ' 403 403 '` = `0` · public IP จริงใน log มีแต่ traffic **port 80** (`/ts2021`) ที่ได้ `301`
  · **ผลจริง:** `/web/` (headscale UI) + `/api/*` เข้าถึงได้จาก internet — **รอดเพราะ headscale มี auth
  ของตัวเอง ไม่ใช่เพราะ ACL**
  · 🔵 **ตัดสินใจ 2026-10-02: คงไว้ (ก)** — เจ้าของยอมรับความเสี่ยง เพราะ headscale มี auth เองอยู่แล้ว
  → ไม่เปิด ticket · ถ้าอนาคตเปลี่ยนใจ: (ข) `auth_basic`/จำกัด path, (ค) แก้ ingress ให้ NPM เห็น client IP จริง
  · ⚠️ **ห้ามแก้ด้วยการ bypass hubrelay ให้ 443 ตรงไป NPM** โดยไม่ทดสอบ — Tailscale client นอกบ้าน
  (`/ts2021`, `/derp`) มาจาก public IP ของ carrier → จะโดน `deny all` → **tailnet ล่ม**
- **Firewall ที่ PVE host: ให้ `106:8080` เหลือแค่ NPM + 109** — ถูกตัดออกจาก ticket 02 (AC 3)
  ตอนนั้นเขียนว่า "ย้ายไป ticket 07" ซึ่ง**ผิด** (07 = key expiry) · CT 106 เป็น unprivileged LXC
  ทำ `iptables`/`nft` ไม่ได้ → ทางที่ถูกคือ PVE host firewall (`cluster.fw`) ซึ่งกระทบทุก CT
  · ยังต้องแก้สเปกบรรทัด 26/44 ด้วย (เขียน "API อ่านได้เฉพาะจาก 109" ซึ่งขัดกับ AC ข้อ 1/2 ของ ticket 02)
- **จดวิธี rotate API key ของ headscale (109)** — ตัดออกมาจาก ticket 07 (Scope ข้อ 4) ตอนที่ตัด ticket 07 ทิ้ง
  · **API key ของ 109 หมดอายุ 2026-12-30** — คนละเรื่องกับ node key
  · ไฟล์: `/etc/dashboard/secrets/headscale.key` (mode 600, โฟลเดอร์ `secrets` mode 700)
  · headscale 0.29.3: `apikeys create` **ไม่มี flag `--user`** (API key เป็นระดับเซิร์ฟเวอร์)
  · ต้องเขียนให้ครบ: สร้าง key ใหม่ → เขียนไฟล์แบบไม่ให้ค่าหลุด (`read -rsp` + `printf '%s'`)
  → ยืนยัน `/api/nodes` ยังคืน 200 (แอปอ่านไฟล์ทุก request ไม่ต้อง restart)
  · 🟡 **ฝั่ง tsguard เขียน runbook เสร็จแล้ว** ที่ `tsguard/docs/rotate-headscale-key.md` — ข้างในระบุให้
  **เช็คก่อนว่า key ของ 109 กับของ tsguard เป็นตัวเดียวกันไหม** (เทียบ sha256) ถ้าต่างกันต้องหมุนแยก
  และถ้าเหมือนกัน การหมุนฝั่ง tsguard จะทำให้ dashboard พังจนกว่าจะไปแก้ที่ 109 ด้วย · orphan นี้ยัง**เปิด**

## กติกา

- **1 ticket = 1 session ใหม่** (เคลียร์ context ระหว่าง ticket), ปิดเองเมื่อ implement เสร็จ
- Seam 1 = Dashboard REST API (03–05, 07) · Seam 2 = tsguard handlers (06) — TDD ทำที่นี่เท่านั้น, UI ไม่ grade
- Tickets 01–02 เป็นงาน infra — ทดสอบด้วยการใช้งานจริงตาม acceptance criteria ในไฟล์
- **`.ps1` ที่มีอักษรไทยต้องมี UTF-8 BOM** (`[IO.File]::WriteAllText($p,$t,(New-Object Text.UTF8Encoding $true))`)
  ไม่งั้น PS 5.1 อ่านเป็น cp1252 แล้วอักษรไทยกลายเป็น quote ตัวโกง → syntax error
  ตรวจก่อน commit ทุกครั้ง: `[Parser]::ParseFile($p,[ref]$t,[ref]$e).Count` ต้องเป็น 0
- เจอขัดข้องที่ทำให้ต้องแก้ spec → หยุดและรายงาน อย่าแก้ทางเอง
