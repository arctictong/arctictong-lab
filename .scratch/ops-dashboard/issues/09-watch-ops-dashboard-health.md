# 09 — tsguard เฝ้าดู ops dashboard: แจ้งเมื่อ CT 109 ตาย

**Blocked by:** — (ขนานกับ 06 ได้เลย · ฝั่ง dashboard รออยู่แล้ว ไม่ต้องแตะ)
**Blocks:** —
**Labels:** seam-2, config, tdd

## ทำไม

หน้ารวมที่ทำมาทั้งหมดไม่มีใครเฝ้า — ถ้า nginx หรือ uvicorn บน CT 109 ตาย จะไม่มีใครรู้
จนกว่าจะไปเปิดหน้าเอง และไม่มีทางแก้เองได้เพราะมันคือตัวที่ตาย

จุดที่ทำให้เรื่องนี้ควรทำตอนนี้เพราะ tsguard คือตัวเดียวที่**ยังทำงานได้** ในสถานการณ์นั้น:
มันอ่าน headscale ตรง ไม่ต้องผ่าน 109 เลย ต่างจากกรณี headscale ตายที่ tsguard จะตาบอดตาม
(sensor ตาย = ตาบอด ซึ่งแก้ไม่ได้; sensor ที่ตายเป็นแค่ตัวแสดงผล = ยังแก้ตัวอื่นได้)

ฝั่ง dashboard เตรียมไว้ครบแล้ว — เหลือแค่คนมาอ่าน

## Context

- **ฝั่ง dashboard รออยู่แล้ว:** `GET /api/health` — `dashboard/src/dashboard/web/app.py:162`
  คืน `{"status": "ok", "service": "dashboard", ...}` และ **ไม่แตะ upstream เลย** (ตั้งใจให้เป็น
  liveness ของตัวมันเอง ไม่ใช่ความสุขภาพของระบบทั้งหมด) — docstring เขียนรอไว้ตรง ๆ ว่า
  *"this is what tsguard polls"*
- **tsguard ยังไม่มีโค้ด poll เลย** — `grep` หา `api/health` / `192.168.1.200` /
  `arctictong-hs` ใน `tsguard/src/` ไม่เจออะไรเลย ไม่มี client สำหรับ 109
- **ทางเดินเครือข่าย: ต้องผ่าน nginx บน 109 ไม่ใช่ต่อ 8001 ตรง** — app ผูก `127.0.0.1:8001`
  ตาม comment ใน `dashboard/nginx/dashboard`: *"Loopback-only bind, so nothing on the LAN
  reaches it except through here"* ⇒ URL คือ `http://192.168.1.200:9080/api/health`
- **ไม่ต้องมี credential** — basic auth อยู่ที่ NPM ไม่ใช่ที่ 109 และ `nginx/dashboard`
  ไม่มี `auth_basic` เลย ⇒ จาก LAN ได้ 200 (ยืนยันแล้วตอนปิด ticket 08)
- **ลูกเลียนที่มีอยู่แล้วให้ยืม** ไม่ต้องคิดของใหม่:
  - `httpx` เป็น HTTP client อยู่แล้ว (`collector/headscale.py`) — ไม่เพิ่ม dependency
  - `EventKind` + `Severity` + `_emit` คือทางเดินของ alert เดิมทั้งหมด
  - คู่ `KV_LAST_*_OK` / `KV_LAST_*_ERROR` = วิธีเก็บสถานะล่าสุดของแต่ละ sensor
  - `_should_report_headscale_error()` = กันเขียน event ซ้ำด้วย cooldown
- **ชื่อ config ต้องระวังชน:** tsguard **มี dashboard ของตัวเอง** (`https://100.64.0.16:8443`,
  `web/serve.py`, `/healthz`) ตั้งชื่อ section ว่า `dashboard` แล้วจะกำกวมทั้งโค้ดและ log
  ⇒ ตั้งชื่อ **`ops_dashboard`**

## Scope

1. **Config `ops_dashboard`** — `enabled: false` เป็นค่าเริ่มต้น (เพิ่ม dependency ไปยังกล่องที่
   ยังไม่มี dashboard ต้องไม่ทำให้ของที่มีอยู่พัง), `url: AnyHttpUrl`, `poll_interval: int >= 60 = 300`,
   `timeout: int >= 1 = 10`, `verify_tls: bool = True`, `down_confirmations: int >= 1 = 2`
   — ต้องตอบ 200 แต่เป็น error ซ้ำ 2 รอบติดกันถึงจะบอกว่าตาย dashboard ที่ถูก restart
   ค้างแค่วินาทีเดียวไม่ควรกลายเป็น alert
2. **`collector/opsdash.py`** — `fetch_health()` แยกให้ชัดว่า *ตอบ 200 แต่ `status != ok`* กับ
   *ต่อไม่ได้ / timeout / 5xx* และคืน `retryable` แบบเดียวกับ `HeadscaleError`
3. **Loop** — เลือก `maintenance_loop` (tick 60 วินาที, `app.py:604`) หรือ loop แยก
   ผมแนะนำ **loop แยก** เพราะ digest ต้องตรงนาทีที่ 0 ของชั่วโมง (`_maybe_digest` เทียบ
   `now.hour == want and now.minute == want`) — timeout 10 วินาทีของ health check ที่ไป
   ดัน digest ให้ตกเวลาได้
4. **EventKind ใหม่** `OPS_DASHBOARD_DOWN` (warning) + `OPS_DASHBOARD_UP` (info ตอนกลับมา)
   ให้เห็นใน `/history` ว่าตายเมื่อไหร่และกลับมาเมื่อไหร่ — ไม่ใช่แค่ log
5. **KV** `ops_dashboard_last_ok` / `ops_dashboard_last_error` (เทียบ pattern เดิม)
6. **Cooldown กัน alert ซ้ำ** — ยืมรูปแบบ `_should_report_*`: ข้อความเดิมใน cooldown = ไม่รายงาน
   ข้อความเปลี่ยน = รายงานใหม่ dashboard ที่ตายทั้งวันต้องไม่เขียน event 288 แถว
7. **Tests** — ปิด feature แล้วไม่ยิง HTTP / ตอบ ok / ตอบ 500 / timeout / 404 (URL ผิด) /
   กลับมาหลังตาย (ต้องมี event UP) / error ซ้ำข้อความเดิมใน cooldown → ไม่มี event ใหม่ /
   `down_confirmations` ทำงานจริง (พลาด 1 ครั้งยังไม่เตือน)

## Acceptance criteria (fail ได้จริง)

- [ ] `pytest` ผ่านทั้ง suite
- [ ] `enabled: false` (ค่าเริ่มต้น) = **ไม่ยิง HTTP ใด ๆ** — test ด้วย client ปลอมที่จะ raise ถ้าถูกเรียก
- [ ] เปิดแล้วแล้วตั้ง URL ผิด → alert มาหนึ่งครั้ง ไม่ใช่ทุก 5 นาที
- [ ] กลับมาแล้ว → event "กลับมาแล้ว" ครั้งเดียว ไม่เงียบกลืน
- [ ] ปิดแล้ว = เงียบจริง ไม่มี event ไม่มี log รบกวน
- [ ] **บนเครื่องจริง (CT 108):** `curl -s http://192.168.1.200:9080/api/health` ตอบ 200
      ⇒ ใส่ config ⇒ เห็น `ops_dashboard_last_ok` เดิน · **ปิด nginx บน 109 ชั่วคราว**
      ⇒ ต้องได้ alert จริงใน Telegram ภายใน ~2 รอบ poll ⇒ เปิดคืน ⇒ ได้ event UP
- [ ] ไม่มี `shell=True` เพิ่ม (ไม่มีอยู่แล้ว)

## คำถามที่ต้องตอบตอนลงมือ

- **URL คือ `http://192.168.1.200:9080/api/health` แน่นอนไหม** — `nginx/dashboard` ในรีโปไม่มี
  `auth_basic` แต่ต้องเช็คของจริงบน 109 ก่อน แล้วเช็คว่า port ที่ deploy จริงฟังคือ 9080
- **ระดับความรุนแรง** — dashboard ตายไม่กระทบ tailnet เลย ควรเป็น `warning` ใช่ไหม
  (ถ้าเป็น `critical` มันจะไปโดน bypass ใน `notifier.bypass_severities` ซึ่ง default เป็น CRITICAL)

## ไม่ทำ

- **ไม่แก้ `/status` ให้ไปอ่าน dashboard** — สเปกบรรทัด 91 เขียนไว้ว่าให้เรียก Dashboard API
  แต่สเปกบรรทัด 98 บอกเองว่า source of truth ของ "online" คือ `lastSeen` จาก headscale
  ซึ่ง tsguard **อ่านตรงอยู่แล้ว** การวิ่งออกไป 109 เพื่อถาม headscale แล้ววิ่งกลับมา = รอบที่ไม่มี
  เหตุผล และทำให้ `/status` พังตาม 109 ทั้งที่ข้อมูลอยู่ในมืออยู่แล้ว
- ไม่ให้ tsguard restart หรือแตะ dashboard ได้ — นอกขอบเขต และขัดกติกา "ห้าม restart กลางวัน"
- ไม่แตะ digest / notifier / state machine เดิม
- ไม่เปิด `8001` ออก LAN และไม่ย้าย `/api/health` ไปไว้ที่ NPM — การเปิดใหม่ที่ไม่จำเป็น
  คือความเสี่ยงที่ไม่ได้แลกกับอะไร