# 03 — `/api/nodes` + หน้ารวม `/` แสดง node summary (โครง dashboard จริง)

**Blocked by:** 02
**Blocks:** 04, 05, 07
**Labels:** seam-1, tdd

## ทำไม

นี่คือ ticket แรกที่สร้าง "dashboard รวม" ของจริง — โครง API (Seam 1) + หน้าแรกที่ดึงมาแสดง
Seam 1 ตกลงไว้ใน spec: ทุก endpoint return JSON เสมอ (error ก็ JSON), ทดสอบที่ชั้นนี้ตัวเดียวโดย mock upstream
UI สวย/ไม่สวยไม่เขียน test — test อยู่ที่ API ชั้นเดียว

## Context

- Repo: `F:\OpenCode\dashboard\` (backend ที่ 109 — จาก ticket 02)
- headscale REST ที่ 106: `GET /api/v1/node` (Bearer key ที่ตั้งไว้ใน 02), **id เป็น STRING**, `expiry`/`lastSeen` เป็น RFC3339 หรือ null, `online` คำนวณจาก lastSeen (ไม่ real-time), camelCase ทั้งหมด — รายละเอียด schema ครบที่ `tsguard/PLAN.md` หัวข้อ 1.3
- Secret path: `/etc/dashboard/secrets` (mode 600) ตาม spec
- ข้อมูล node "ออนไลน์" ที่มนุษย์คาดหวัง = ตรงกับที่ headscale web เห็น (source of truth เดียว: headscale API)

## Scope

1. **Endpoint `GET /api/nodes`** — shape ตาม spec, TDD:
   ```jsonc
   {
     "nodes": [
       { "id": "1", "name": "pchome", "online": false, "lastSeen": "...",
         "ip": "100.64.0.1", "os": "windows", "tags": ["myhome"],
         "keyExpiry": "2026-11-01T00:00:00Z" }   // null ได้ = ไม่มีวันหมดอายุ
     ],
     "generated_at": "..."
   }
   ```
   - upstream error → ยังเป็น JSON: `{ "error": "..." }` + HTTP 502/503 (ไม่เด้ง HTML)
2. **หน้า `/` ของ 109** — หน้าแรก dashboard รวม (ตอนนี้มี panel เดียว): ตาราง node จาก `/api/nodes`, สีเขียว/แดงตาม online, แสดง last seen แบบมนุษย์ ("2h ago"), แสดง tag
3. **Tests (pytest ใน `dashboard/`):** mock headscale API (ตาม pattern ที่ `tsguard/tests/test_headscale.py` ทำอยู่ — fixtures มี payload จริงให้ลอกได้), ครบ: สำเร็จ / upstream ตาย / payload มี node ที่ `expiry=null`

## Acceptance criteria (fail ได้จริง)

- [x] `pytest` ใน `dashboard/` ผ่าน — 3 tests, ทั้งหมดผ่าน (mock upstream)
- [x] `curl -s http://109/api/nodes | jq '.nodes | length'` = จำนวน node ตรงกับ `tailscale status` บน PC ทุกตัว และสถานะ online/offline ตรงกับ devices.html — deploy ไป 109 แล้ว: `/api/nodes` = **17 node**; เทียบกับ `tailscale status --json` บนเครื่องนี้ (16 peer + self) ด้วย IP เป็นกุญแจ → **0 mismatch, 0 node ที่มีแต่ใน dashboard**; สถานะตรงกับ devices.html เพราะ devices.html อ่าน headscale API ตัวเดียวกัน
- [x] `curl -s http://109/api/nodes -o /dev/null -w '%{content_type}'` = `application/json` **แม้ปลด LAN สาย headscale** (upstream ตายแล้วยังตอบ JSON) — พิสูจน์ผ่าน tests (mock error → 503 + JSON) และเพิ่ม `Exception` handler ให้บั๊กของแอปเองก็ตอบ JSON
- [x] เปิด `http://109/dashboard/` เห็นตาราง node ครบทุกตัว พร้อมสีตามสถานะ ณ ตอนนั้น (ของที่ offline นาน เช่น myipad ต้องเป็นแดง) — ครบ 17 แถว, `arctictong`/`DESKTOP-CDFJ4TV`/`localhost`(=myipad)/`Tong-NB` แดง, ที่เหลือเขียว

### หมายเหตุจากการ deploy จริง

- `/api/nodes` ครั้งแรกตอบ **500 เป็น HTML** เพราะ `_node_row` อ้าง `node.ID/Online/Tags/Expiry` (โมเดลใช้ตัวพิมพ์เล็ก) และเรียก `last_seen_human` แบบไม่ใส่ `()` — เทสเดิมไม่จับเพราะ node list ว่างเสมอ (key หาย / upstream ตาย) แก้แล้ว + เพิ่มเทสที่ป้อน node จริง (commit `e0330c2`)
- ป้ายชื่อ node ใช้ `name` ของ headscale = **hostname ดิบ** (เช่น `localhost`, `WIN-N07QU6H1DG0`, `097184-W`) ขณะที่ tailscale แสดง `givenName` (เช่น `myipad`, `dip`, `fda-web`) — ยังไม่แก้ รอตัดสิน (ดูหัวข้อ "ค้าง")
- `http://109:9080/` ตอบ **403** มาตั้งแต่ ticket 02 (ไม่ใช่ของใหม่): docroot `/var/www/headscale-ui/` มีแต่ `web/` ไม่มี `index.html` ที่ราก → `try_files` ตกลงที่ directory แล้ว `index` หาไม่เจอ; UI จริงอยู่ที่ `/web/` (200) ซึ่งเป็นทางที่ NPM ใช้

## ไม่ทำ

- ไม่แตะ tsguard, ไม่แตะ hsbridge
- ไม่ทำ services/PVE panel (ticket 04/05), ไม่ทำ key expiry warning (ticket 07)
- ไม่ทำ auth ใหม่ — ใช้กลไกกันเองแบบเดิมที่ devices.html ใช้
