# 07 — keyExpiry warning + digest แจ้ง node key ใกล้หมดอายุ

**Blocked by:** 03
**Blocks:** —
**Labels:** seam-1, optional

**สถานะ: ⊘ ตัดออก (2026-10-01)** — เจ้าของตัดสินใจว่าไม่จำเป็น ผลที่ตามมา: **node key ที่หมดอายุจะยังเงียบ**
(node หลุดจาก tailnet ข้ามวันโดยไม่มีการเตือน) และ digest จะไม่มีบรรทัดนี้ · AC ทั้ง 4 ข้อไม่ถูกทำ
· ยกเว้น **Scope ข้อ 4** (note วิธี rotate API key) ที่ย้ายไป **"งานที่ยังค้าง (orphan)" ใน board**
เพราะ API key ของ 109 หมดอายุ **2026-12-30** จริง ๆ — คนละเรื่องกับ node key

## ทำไม

Node หลายตัวในบ้าน (myipad, tong-nb) offline ข้ามวันเพราะ **node key หมดอายุเงียบๆ** — headscale ไม่แจ้ง
Spec ระบุ field `keyExpiry` ไว้แล้วตั้งแต่ ticket 03 — ticket นี้ทำให้มัน "มีความหมาย": เตือนล่วงหน้า 7 วัน ทั้งบนหน้า `/` และใน digest เช้า
(สถานะ optional — ถ้างานเบาอยู่แล้วทำ, ถ้าโดนเวลากดให้ตัดเป็นอย่างแรก)

## Context

- Repo: `F:\OpenCode\dashboard\` (ส่วน `/` + `/api/nodes`) และแตะ digest ที่ `F:\OpenCode\tsguard\src\tsguard\notifier.py` (`send_digest` มีอยู่แล้ว)
- headscale: `expiry` เป็น RFC3339 หรือ **null = ไม่มีวันหมดอายุ** (ไม่ต้องเตือน); API key ของ dashboard เองก็หมดอายุ 90 วัน — ระวังสับสนระหว่าง "node key expiry" กับ "API key expiry" คนละเรื่องกัน

## Scope

1. **Dashboard:** ใน `/api/nodes` เพิ่ม field `keyExpiryWarning: bool` (≤7 วันจาก generated_at = true; null = false) — TDD ตาม Seam 1, mock payload ที่มี expiry ใกล้/ไกล/null
2. **UI:** แถว node ที่ warning → เครื่องหมาย/สีเหลืองพร้อมข้อความ "key หมดอายุในอีก N วัน"
3. **tsguard digest:** เพิ่มส่วนใน `send_digest` — query dashboard `/api/nodes` (ดึงตามวิธีที่ 03 ทำให้ JSON เสมอ), ถ้ามี node ใกล้หมดอายุ → บรรทัดเตือนใน digest พร้อมชื่อ node + วันเหลือ; ไม่มีใครใกล้ → ไม่พูดถึง (digest ปกติ)
4. ทำ **note วิธี rotate API key ของ dashboard** ใน `dashboard/README.md` (โยงกับความเสี่ยงจาก ticket 02)

## Acceptance criteria (fail ได้จริง)

- [ ] `pytest` ผ่าน (dashboard + tsguard ทั้งสอง repo) — **ก่อนเริ่ม `/api/nodes` ไม่มี field `keyExpiryWarning`**
- [ ] ตั้ง expiry ของ node ทดสอบหนึ่งตัวให้เหลือ ≤7 วัน (ผ่าน `headscale nodes expire`/re-register หรือแก้ clock ใน test) → หน้า `/` แสดงเหลืองพร้อมจำนวนวันจริง
- [ ] วิ่ง digest ด้วยมือ (`uv run tsguard ... digest` หรือทางที่โค้ดมี) พร้อม node ใกล้หมดอายุ → รับข้อความแจ้งชื่อ + วันเหลือใน Telegram; ไม่มี node ใกล้หมด → digest ไม่มีบรรทัดนี้
- [ ] Node ที่ `expiry=null` ไม่ถูกตีเป็น warning ทุกกรณี (มี test คลุม)

## ไม่ทำ

- ไม่ทำ auto-expire/renew อะไรทั้งนั้น (headscale มี POST /node/{id}/expire แต่นั่นคนละทิศ — ห้ามแตะ)
- ไม่ทำ alert ทันทีนอก digest — ให้อยู่ใน digest เช้าอย่างเดียว (กัน spam)
