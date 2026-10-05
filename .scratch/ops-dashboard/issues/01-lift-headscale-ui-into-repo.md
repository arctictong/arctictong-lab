# 01 — Prefactor: ยก headscale web UI จาก LXC 106 เข้า version control

**Blocked by:** — (เริ่มได้ทันที)
**Blocks:** 02
**Labels:** prefactoring, infra

## ทำไมต้องมี ticket นี้

`devices.html` (User/Device View/Settings UI) ตอนนี้ deploy มืออยู่บน LXC 106 (Headscale-Server) เท่านั้น
ไม่มีอยู่ใน git ที่ F:\OpenCode เลย (มีแค่ tsguard/, hsbridge/, note-app/)
งานทั้งหมดที่เหลือ (ย้ายไป 109, ต่อยอด dashboard) จะไปแก้ของที่ไม่มี backup และไม่มี deploy path — ทำให้ง่ายก่อน แล้วค่อยทำสิ่งที่ง่าย

## Context (สำหรับ session ที่ไม่เคยเห็นระบบ)

- LXC 106 = CT บน Proxmox "pve", ชื่อ Headscale-Server, รัน headscale 0.29.x
- UI ตัวปัจจุบันถูก serve ที่ `https://arctictong-hs.duckdns.org/web/devices.html` (ผ่าน NginxProxyManager CT 101 → 106) — **ชื่อ domain ที่เขียนไว้ตอนแรกผิด (ไม่มีขีด) แก้แล้วใน `fe82358`**
- เข้าถึง LXC: `ssh root@<106-ip>` (ดู IP ได้จาก `tailscale status` หรือ console PVE) — หา root dir ของ nginx จาก `/etc/nginx/sites-enabled/*`
- กติกาสไตล์ repo นี้: แต่ละโปรเจกต์มี `deploy.ps1` ของตัวเอง (ดูแพทเทิร์นที่ `tsguard/deploy.ps1`)

## Scope

1. SSH เข้า 106, ค้นหาไฟล์ static ที่ serve หน้า `/web/` (nginx root + ทุกไฟล์ที่ devices.html อ้างถึง: css/js/icons)
2. ก๊อปมาไว้ใน repo ใหม่: `F:\OpenCode\dashboard\` โครงสร้างตามที่พบบน 106 ตรงๆ **แก้โค้ดอะไรไม่ได้เลยใน ticket นี้**
3. ระบุใน `dashboard/README.md`: ไฟล์แต่ละตัวทำอะไร, backend จริงของ UI นี้คืออะไร (ถ้า devices.html ยิง API ออกนอกหน้า — จาก network tab หรืออ่านโค้ด: สังเกตว่ามันคุยกับ headscale REST ตรงๆ หรือผ่าน server-side script บน 106), และบันทึก "deploy ปัจจุบัน = มือ" ไว้
4. เขียน `dashboard/deploy.ps1` — ยิงไฟล์จาก repo ไปยัง 106 ตาม path เดิม (ssh/scp, pattern เดียวกับ tsguard/deploy.ps1)
5. อัปเดต `.gitignore` ระดับ repo ถ้าจำเป็น, commit

## Acceptance criteria (ต้อง fail ก่อนเริ่ม — ตรวจได้จริง)

- [x] `F:\OpenCode\dashboard\` มีไฟล์ UI ครบทุกไฟล์ที่ 106 serve, และ `git status` สะอาดหลัง commit — **ก่อนเริ่ม โฟลเดอร์นี้ไม่มีอยู่จริง**
- [x] ลบไฟล์หนึ่งไฟล์บน 106 (เช่น css) → รัน `dashboard/deploy.ps1` → หน้า `https://arctictong-hs.duckdns.org/web/devices.html` กลับมา render ถูกต้องครบเหมือนเดิม (ทั้ง CSS, รายการ device, ปุ่มทุกปุ่ม)
- [x] `dashboard/README.md` อธิบายได้ว่า UI คุยกับ backend ทางไหน (endpoint ชื่ออะไร, ใส่ auth ยังไง) — ไม่มีประโยค "ไม่แน่ใจ"

## ผลรัน (ปิด 2026-09-30, commit `607f857` + `fe82358`)

- 27 ไฟล์ใน `dashboard/web/` = ทุกไฟล์ที่ nginx เสิร์ฟ, blob hash ใน git ตรงกับดิสก์ 27/27
  (`.gitattributes` ตั้ง `web/**` เป็น `-text` เพราะเครื่องนี้ `core.autocrlf=true`)
- **AC2 ผ่าน**: ลบ `0.BQaBh7y2.css` (ไฟล์ที่ทุกหน้า `<link>` อ้างถึง) บน 106
  → รัน `deploy.ps1` → กลับมาครบ 27 ไฟล์ mtime เดิม → ดึงจาก URL สาธารณะมาเทียบ
  sha256 ตรงกับ repo ทั้ง 11 ไฟล์ที่เช็ค
- **เจอของแถม 3 อย่าง ต้องรู้ก่อนทำ ticket ถัดไป**:
  1. **domain จริงมีขีด** — `arctictong-hs.duckdns.org` (ตรงกับชื่อ site ใน nginx)
     ชื่อที่เขียนไว้เดิม `arctictonghs` ไม่มี A record เลย
  2. **NPM route `/api/` ไปหา headscale บน 106** — พิสูจน์จาก `curl -i
     /api/v1/user` ที่ตอบ `401 Unauthorized` พร้อม headscale header
     (`frame-ancestors 'none'`, `X-Frame-Options: DENY`) ไม่ใช่ header ของ nginx
     → same-origin, **ไม่ต้องเปิด CORS ไม่ต้องเปิดพอร์ต headscale ออก**
  3. `_app/immutable/assets/_layout.BQaBh7y2.css` เป็นไฟล์ตาย — ไม่มีอะไรอ้างถึง
     (hash เหมือน `0.BQaBh7y2.css` เป๊ะ) ไม่ได้แตะตามข้อห้าม
- **หน่วง 2 อย่างที่ยังไม่ได้ทำ (ไม่ใช่ของ ticket นี้)**:
  - UI นี้เป็น **build artefact ไม่มี source** — จะแก้หน้าตาไม่ได้จนกว่าจะมี source
  - config เก่า `/etc/nginx/sites-available/arctictong-hs` ยังไม่ได้ symlink
    (nginx ไม่อ่าน) — ไม่แตะ เพราะ ticket นี้ห้ามแก้ config

## ไม่ทำใน ticket นี้

- ห้ามแก้โค้ด UI แม้แต่ตัวอักษรเดียว (no drive-by fixes)
- ห้ามสร้าง LXC ใหม่ / ห้ามแตะ headscale config
