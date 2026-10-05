# 04 — `/api/services` + panel service health

**Blocked by:** 03
**Blocks:** —
**Labels:** seam-1, tdd

## ทำไม

Panel ที่สองของหน้า `/dashboard/`: เห็น service ไหนตายเป็นแดงทันที ไม่ต้องเปิด Nagios แยก
นี่คือของที่ตอนนี้ต้องเปิดหลายที่มารวมกันใน dashboard จุดเดียว

## Context

- Repo: `F:\OpenCode\dashboard\`, ต่อจากโครง API ที่ 03 วางไว้ (pattern เดียวกัน: TDD + mock upstream + JSON เสมอ)
- **ของจริงบน NPM (CT 101) ไม่ตรงกับที่ ticket นี้เขียนไว้ตอนแรก** — `grep` ใน `/data/nginx/proxy_host/`
  เจอ proxy host แค่ 3 ไฟล์ และหนึ่งในนั้นคือ headscale เอง:

  | NPM host | `server_name` | `proxy_pass` |
  |---|---|---|
  | `2.conf` | `arctictong-ha.duckdns.org` | `192.168.1.248:8123` — HomeAssistant |
  | `3.conf` | `arctictong-notes.duckdns.org` | `192.168.1.196:8090` — NoteApp |
  | `12.conf` | `arctictong-hs.duckdns.org` | `192.168.1.200:9080` — headscale + dashboard |

  → Nextcloud, WebServer, RustDesk, hubrelay **ไม่มี proxy host เลย** ข้อที่เขียนว่า
  "ต้องเป็น URL ที่ NPM serve จริง ไม่ใช่ IP ตรง" ใช้ได้กับแค่ 2 ตัว
- **hairpin NAT ใช้ได้** — จาก 109 ยิง `https://arctictong-{ha,notes}.duckdns.org` ได้ `200` ใน ~275ms
  จึงพร่องผ่าน NPM จริง (DNS → router → NPM → app) ได้ตามเจตนา ไม่ต้องยิง IP ตรง
- สองตัวที่เหลือ **ไม่มี HTTP ให้พร่อง**:
  - RustDesk — `rustdesk-server` ไม่มี web endpoint; เปิด 21115/21116/21117 (21118/21119 ปิด)
  - hubrelay — status endpoint ที่ `192.168.1.198:8686` ตอบ **`401`** เพราะบังคับ Bearer token
    (`hsbridge/internal/config/config.go` → `status_token` required) ถ้า probe ด้วย URL จะได้ `warn`
    ตลอดกาล ซึ่งหมายถึง "เราไม่ได้พา token มา" ไม่ใช่ "relay ตาย" และการยัด token ลงไฟล์ config
    ที่แอปอ่านทุก request ก็ผิดกติกา secret
- **ตัดออกโดยเจตนา**: Nextcloud (`.85` — เจ้าของใช้บ้างไม่ใช้บ้าง, ภายในล้วน), WebServer (`.195` —
  "เปิดไว้เฉยๆ ไม่ได้ใช้งาน"), Nagios (เจ้าของสั่งตัด) แถวแดงของของที่ไม่มีใครใช้คือ noise
  ที่ทำให้คนเลิกอ่าน dashboard — หลักการเดียวกับ `os: null` ของ ticket 03
- Shape ตาม spec:
  ```jsonc
  { "services": [ { "name": "Nextcloud", "url": "https://...", "state": "up|down|warn", "latency_ms": 12 } ] }
  ```

## Scope

1. **Config รายการ services** — `services.yaml` บน 109 (`/etc/dashboard/services.yaml`) แต่ละรายการ:
   name, url, timeout, warn threshold และ (ใหม่) `type`
   - `type: http` (default) — GET `url`, ตัดสินด้วย `classify()` เดิม
   - `type: tcp` — เปิด socket ไป `host:port` แล้วปิด ไม่พูด protocol; url ที่แสดง derive เป็น
     `tcp://host:port` เพื่อให้ API กับหน้าเว็บยังอ่าน field เดียว
2. **Endpoint `GET /api/services`** — วิ่ง probe HTTP แบบ concurrent (asyncio), จับ timeout/SSL error
   เป็น `down`, 2xx = `up`, 4xx/5xx หรือ latency เกิน threshold = `warn`; TDD ด้วย mock HTTP server
   (TCP probe ทดสอบกับ listening socket จริง)
3. **Panel "Services" บนหน้า `/dashboard/`** — แถวละ service: จุดสี + ชื่อ + latency, auto-refresh ตาม pattern
   ที่ 03 ใช้ · แถว `tcp://` แสดงเป็นข้อความไม่ใช่ลิงก์ (browser เปิดไม่ได้)
4. กติกาเดิม: error ทุกแบบตอบ JSON ไม่ใช่ HTML

## Acceptance criteria (fail ได้จริง)

- [x] `pytest` ผ่าน — **102 tests** (headscale model, probe logic http+tcp, config, API surface)
      endpoint `/api/services` มีทั้ง unit+integration tests · มี regression test ที่ป้อน node จริง
      (บั๊กที่หลุดใน ticket 03) และ integration test ที่ยิง TCP จริงผ่านทั้งแอป
- [x] `curl http://109/api/services` แสดงครบทุก service ตาม config และ HomeAssistant เป็น `up` จริง
      ณ เวลานั้น — Nextcloud ถูกตัดออกตาม Context ข้างบน
- [x] หยุด service หนึ่งจริง (`pct stop 105` NoteApp) → หน้า `/dashboard/` แสดง NoteApp เป็นแดง
      → `pct start 105` → กลับเขียว
- [x] Latency แสดงเป็นตัวเลขจริง (ไม่ใช่ 0/null ตลอด)

### หลักฐานจากการ deploy จริง (CT 109, 2026-10-01)

```
up   308ms  HomeAssistant  https://arctictong-ha.duckdns.org   code=200
up   294ms  NoteApp        https://arctictong-notes.duckdns.org code=200
up     1ms  RustDesk       tcp://192.168.1.193:21116            code=None
up     1ms  hubrelay       tcp://192.168.1.198:8686             code=None
summary {"up": 4, "warn": 0, "down": 0}
```

AC ข้อ 3 (`pct stop 105`):

```
down  5288ms  NoteApp  code=None timeout after 5s
<td><span class="dot down-bg"></span>NoteApp</td>      <- หน้า /dashboard/

up      19ms  NoteApp  code=200                        <- หลัง pct start 105
<td><span class="dot up-bg"></span>NoteApp</td>
```

หมายเหตุ: ตอน CT 105 ตาย ผลคือ **timeout 5s** ไม่ใช่ `502` — ทางวิ่งเป็น public URL ที่วนออกนอกบ้าน
แล้วกลับเข้า NPM ดังนั้น NPM จึงค้างจนครบ timeout ของ probe ไม่ได้ปฏิเสธทันที `classify()` ยังนับเป็น
`down` ถูกต้อง แต่ถ้าอนาคตอยากได้ `502` ที่รวดเร็วขึ้น ต้อง probe ด้วย Host header ยิงเข้า NPM ตรง ๆ
(ยังไม่ทำ — ดู "ไม่ทำ")

### Rollback ของ config

`/etc/dashboard/services.yaml` **ไม่ได้อยู่ใน git** (แบบเดียวกับ `secrets/headscale.key`) —
deploy ไม่ทับ แต่ถ้า CT 109 ถูกสร้างใหม่ต้องเขียนใหม่ สำเนาที่ intent ตรงกันอยู่ที่
`dashboard/config/services.example.yaml` · แก้ไฟล์แล้วไม่ต้อง restart อะไร (แอปอ่านทุก request)

## ไม่ทำ

- ไม่ทำ PVE panel (ticket 05)
- ไม่ทำ alert/push จาก dashboard — เรื่องแจ้งเตือนยังเป็นหน้าที่ของ tsguard
- ไม่เก็บ history ของ service uptime (SQLite ไว้รอบ 2)
- **ไม่รองรับ custom header / bearer token ใน services.yaml** — จะได้ `502` เร็วกว่า timeout ก็จริง
  แต่แลกมาด้วย secret ในไฟล์ที่อ่านทุก request ซึ่งผิดกติกาที่ตั้งไว้ตั้งแต่ต้น
