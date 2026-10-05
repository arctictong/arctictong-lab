# 08 — เปิด `/dashboard/` ให้เข้าถึงได้จากข้างนอก โดยคง ACL เดิม

**Blocked by:** 03, 05
**Blocks:** —
**Labels:** seam-infra, optional

> ✅ **สถานะ 2026-10-02: ปิดแล้ว — ย้อนกลับสำเร็จ (ไม่ทำต่อ)** · เจ้าของเลือก "ดูแค่ภายใน" จึง
> **ลบ Custom Location `/dashboard/` ออก** ⇒ `/dashboard/` กลับเป็น `404` ให้ทุกคนบน public host
> · วัดผลจริง: `/dashboard/` `404` (ไม่มี `X-Served-By`) · `/web/` `200` · `/api/nodes` `401` ·
> LAN `http://192.168.1.200:9080/dashboard/` `200` · **ในบ้านใช้ได้ครบเหมือนเดิม**
>
> 🧠 **สาเหตุที่ทำให้ต้องย้อนกลับ:** `192.168.1.198` = **hubrelay (CT 107)** และ router
> **forward 443 → hubrelay** ⇒ NPM เห็น external HTTPS ทุกครั้งเป็น `.198` (อยู่ใน allow-list)
> ⇒ **ACL ใช้กับ 443 ไม่ได้เชิงสถาปัตยกรรม** → เปิด `/dashboard/` ออก internet ไม่ปลอดภัย
> · ความเสี่ยงที่เหลือ (`/web/` + `/api/*`) แยกไป orphan

## ทำไม

วันนี้ dashboard ดูได้แต่ในบ้าน (`http://192.168.1.200:9080/dashboard/`)
บน public host `/dashboard/` ถูกตอบโดย **headscale** → `404 page not found` (วัดจริง ไม่ใช่เดา)
เพราะ NPM `proxy_host.id = 12` มี main forward → headscale และมี Custom Location แค่ `/web/`

เจ้าของต้องการดูจากข้างนอก **โดยไม่เปิดให้ internet ทั้งโลก** — ทางที่เลือกคือใช้ ACL เดิม
(LAN + Tailscale) เป็นกำแพง แล้วเปิดแค่ path ของหน้าเว็บ

## Context

- NPM = **CT 101** (Ubuntu 22.04) เป็น docker container ชื่อ `nginx-proxy-manager-app-1`
  data bind-mount ที่ `/opt/nginx-proxy-manager/data` · UI ที่ `http://192.168.1.101:81`
- `proxy_host.id = 12` (`arctictong-hs.duckdns.org`):
  - **main forward → `192.168.1.197:8080` (headscale)** = ทางของ `/api/v1/*` ที่ UI ใช้ — **ห้ามแตะ**
  - Custom Location `/web/` → `192.168.1.200:9080` (CT 109)
  - **ACL อยู่ที่ Custom Nginx Configuration ระดับ host (`server{}`)** → ตามสเปก nginx ครอบทุก location
    ที่ไม่มี `allow/deny` ของตัวเองให้อัตโนมัติ
    `allow 192.168.1.0/24; allow 100.64.0.0/10; allow 10.8.0.0/24; deny all;`
    → **ไม่ต้องใส่ ACL ซ้ำใน location ใหม่**
    ❌ **ยืนยันแล้ว 2026-10-02: ACL นี้กัน 443 ไม่ได้** เพราะ ingress 443 ถูก **hubrelay (CT 107,
    `.198`)** relay มาก่อน → NPM เห็น source เป็น `.198` ซึ่งอยู่ใน allow-list · `deny all;` ไม่มี
    โอกาสทำงาน → **ห้ามพึ่ง ACL นี้เป็นกำแพงบน host นี้** (ดูหลักฐานท้ายไฟล์)
- หน้า `/dashboard/` เป็น **server-rendered ล้วน**: ไม่มี `fetch(`, ไม่มี `XMLHttpRequest`,
  ไม่มี `<link>`, ไม่มี `<script src>` — CSS อยู่ใน `<style>`, refresh ด้วย `location.reload()`
  (ยืนยันด้วยการ grep template จริง) → **เปิดแค่ `/dashboard/` ก็ครบ ไม่ต้องเปิด `/api/` เลย**
- วัดได้ก่อน ticket นี้: `/web/` → `200` (ถึง 109) · `/api/nodes` → `401 Unauthorized`
  (`text/plain` 12 bytes จาก headscale) · `/dashboard/` → `404 page not found`

## Scope

1. เพิ่ม **Custom Location** ใน `proxy_host.id = 12`:
   - Path: `/dashboard/` (ต้องมี `/` ท้าย)
   - Forward Hostname/IP: `192.168.1.200` · Port: `9080` · Scheme: `http`
2. **ไม่แตะ** main forward, `/api/*`, `/web/`, ACL, certificate
3. ยืนยันว่าหน้าทำงานครบจากข้างนอก และ headscale UI ยังปกติ

## Acceptance criteria (fail ได้จริง)

- [x] `curl -s -o /dev/null -w '%{http_code}' https://arctictong-hs.duckdns.org/dashboard/` → `200`
      (ก่อนหน้า = `404`)
- [—] ~~เปิดจากอุปกรณ์ที่ไม่ได้อยู่ใน LAN (มือถือ 4G)~~ **N/A — ยกเลิก** เพราะเจ้าของเลือกดูแค่ภายใน
      จึงถอดช่องออก (`/dashboard/` จาก internet = `404`) ไม่ต้องทดสอบมือถือ
- [x] **headscale UI ไม่พัง:** `https://arctictong-hs.duckdns.org/web/devices.html` ยัง `200`
      และหน้า Settings ยังคุย `/api/v1/*` ได้ (ไม่มี 401 ใหม่)
- [x] **ไม่ได้เปิด `/api/`:** `https://arctictong-hs.duckdns.org/api/nodes` ยังเป็น `401 Unauthorized`
      เหมือนเดิมเป๊ะ (ถ้ากลายเป็น `200` = เผลอเปิด `/api/` ต้องรีบย้อนกลับ)
- [—] **ACL ยังทำงาน:** ❌ **ตกถาวรบน host นี้ — แต่ไม่ block ticket** เพราะถอดช่องออกแล้ว
      · สาเหตุ: ingress 443 → hubrelay (`.198`) → NPM เห็น external เป็น `.198` (allowed) ⇒
      `deny all;` ไม่มีโอกาสทำงาน · `grep -c ' 403 403 '` = `0` · ย้ายไป orphan "ACL กัน 443 ไม่ได้"
- [x] กด Save ใน NPM แล้วผ่าน = `nginx -t` ผ่าน (NPM เช็คให้ตอน Save)

## ไม่ทำ

- ไม่เปิด `/api/*` ออก public — หน้าเว็บไม่ต้องการ และ `/api/` เป็นของ headscale (เปิดเมื่อไหร่ UI พังทันที)
- ไม่แตะ main forward / Forward Hostname ของ host หลัก
- ไม่เปลี่ยน ACL เดิม และ **ห้ามตัด `100.64.0.0/10`** (Tailscale bootstrap)
- ~~ไม่ทำ authentication รอบนี้~~ → เคยพิจารณา `auth_basic` แต่เจ้าของเปลี่ยนใจเป็น **ดูแค่ภายใน** →
  **ถอด Custom Location ออกแทน** · ถ้าอนาคตอยากเปิดจากข้างนอก ให้ใช้ `auth_basic` เฉพาะ location
  `/dashboard/` เท่านั้น — **ห้ามใส่ระดับ host** เพราะจะพัง `/derp`/headscale

## ย้อนกลับ (rollback)

ลบ Custom Location `/dashboard/` แถวนั้นออกใน NPM UI แล้ว Save → กลับเป็น `404` เหมือนเดิม
**ไม่กระทบอย่างอื่นเลย** เพราะ main forward / `/api/` / `/web/` ไม่เคยถูกแตะ

> 🔴 **ทางนี้คือวิธีปิดช่องที่ใช้ได้ทันที** ในเมื่อ ACL ไม่กันจริง: ลบ Custom Location `/dashboard/`
> 1 คลิก → หน้าแผนที่เครือข่ายกลับไปเป็น `404` ให้ทุกคน (เหมือนก่อน ticket 08)
> ไม่ต้องแตะ ACL / main forward และไม่ต้องให้ nginx reload สำเร็จก็ได้ผล (NPM Save = regenerate + reload)

> หมายเหตุ: การแก้ไฟล์ `/data/nginx/proxy_host/12.conf` ตรง ๆ แล้ว reload **ไม่ใช่ทางย้อนกลับที่ใช้ได้**
> เพราะ NPM สร้าง config จาก DB (`/data/database.sqlite`) — แก้ไฟล์แล้ว Save ครั้งถัดไปจะทับหาย
> ต้องแก้ผ่าน UI เท่านั้น
> ⚠️ ข้อเดียวกันนี้คือ **ตัวต้องสงสัยอันดับ 1** ของ ACL ที่ไม่ทำงาน: ถ้า ACL ถูกใส่ในไฟล์ตรง ๆ
> (ไม่ผ่าน UI) มันจะไม่ถูก reload และจะหายเมื่อ Save ครั้งถัดไป

## ทางแก้ที่เลือก (2026-10-02) — ย้อนกลับ (ดูแค่ภายใน)

เจ้าของตัดสินใจ **ไม่เปิด `/dashboard/` ออก internet อีก** เพราะ ACL กัน 443 ไม่ได้ (hubrelay relay)
และไม่อยากได้ auth เพิ่ม → ทางที่ง่ายและปลอดภัยสุดคือ **ลบ Custom Location `/dashboard/`**

1. NPM UI → Hosts → Proxy Hosts → `arctictong-hs.duckdns.org` → Edit → **Custom Locations** →
   แถว `/dashboard/` → **ลบ (ถังขยะ)** → **Save**
2. (ถ้าเผลอสร้างไฟล์รหัสไปแล้ว) `docker exec nginx-proxy-manager-app-1 rm -f /data/nginx/custom/dashboard.htpasswd`
3. วัดผล: `/dashboard/` กลับเป็น `404` (headscale) และไม่มี `X-Served-By: ct109` ·
   `/web/` ยัง `200` · `/api/nodes` ยัง `401` · LAN `http://192.168.1.200:9080/dashboard/` ยัง `200`

**หมายเหตุ:** ACL ของ host นี้ **ยังกัน 443 ไม่ได้อยู่ดี** — ตั๋วนี้ปิดด้วยการ "ถอดช่องออก" ไม่ใช่ "ซ่อมกำแพง"
· ความเสี่ยงที่เหลือ (`/web/` + `/api/*` เปิดจาก internet) แยกไปจัดการต่างหาก

## หลักฐาน

### AC 1/3/4/6 — วัดจากเครื่องนี้ (Windows) หลังกด Save

```txt
$ curl.exe -s -D - -o NUL https://arctictong-hs.duckdns.org/dashboard/
HTTP/1.1 200 OK
Server: openresty
Content-Type: text/html; charset=utf-8
Content-Length: 11229
X-Served-By: ct109                          <-- หลักฐานชี้ขาด: NPM ส่งไป 109 ไม่ใช่ headscale
Strict-Transport-Security: max-age=63072000; preload

$ curl.exe -s -o NUL -w "web/        -> %{http_code}\n" https://arctictong-hs.duckdns.org/web/
web/        -> 200
$ curl.exe -s -o NUL -w "api/nodes   -> %{http_code}\n" https://arctictong-hs.duckdns.org/api/nodes
api/nodes   -> 401
$ curl.exe -s -o NUL -w "api/v1/user -> %{http_code}\n" https://arctictong-hs.duckdns.org/api/v1/user
api/v1/user -> 401
```

หน้าเว็บที่ได้จริง (11 229 bytes) มีข้อมูลครบทั้ง 3 panel:

```txt
$h.Length                 -> 11229
$h.Contains('HomeAssistant') -> True
$h.Contains('NoteApp')       -> True
$h.Contains('Proxmox')       -> True
```

**`X-Served-By: ct109`** คือตัวตัดสินทุกอย่าง — nginx บน 109 ใส่ header นี้ให้ทุก response
`/dashboard/` มี header นี้ แต่ `/api/nodes` ไม่มี (headscale เป็นคนตอบ) → routing แยกกันจริงตามที่ออกแบบ

### ก่อน vs หลัง

| path | ก่อน ticket 08 | หลัง |
|---|---|---|
| `/dashboard/` | `404 page not found` (headscale) | `200` + `X-Served-By: ct109` |
| `/web/` | `200` | `200` (ไม่เปลี่ยน) |
| `/api/nodes` | `401` (headscale) | `401` (ไม่เปลี่ยน) |
| `/api/v1/user` | `401` (headscale) | `401` (ไม่เปลี่ยน) |

### สิ่งที่แก้จริง

Custom Location **แถวเดียว** ใน NPM `proxy_host.id = 12`:

```txt
/dashboard/  ->  http://192.168.1.200:9080
```

main forward · `/api/` · `/web/` · ACL · certificate **ไม่ถูกแตะเลย**

### ⚠️ AC 5 — ทดสอบรอบแรกใช้ไม่ได้ (ถอนข้อสรุป 2026-10-02)

> 🔄 **ถอนข้อสรุป:** probe ผ่าน hackertarget รอบ 2026-10-01 **ไม่ได้ทดสอบ ACL เลย** — พออ่าน log จริง
> เจอว่ามันเข้าถึง NPM ด้วย `[Client 192.168.1.198]` (10:22:16 · UA `Chrome/53 Mac OS X 10_11_6`)
> ซึ่ง **อยู่ใน allow-list** ⇒ `404`/`401` ที่ได้ = ACL **อนุญาต** ไม่ใช่ ACL พัง
> · หลักการ "source จาก internet ต้องไม่อยู่ใน allow-list" ยังถูก แต่ใช้ไม่ได้ถ้า traffic
> ถูก NAT/relay จนกลายเป็น `192.168.1.198` ก่อนถึง NPM

ยิงผ่าน `https://api.hackertarget.com/httpheaders/?q=<url>` — บริการนี้คืนแค่ **header ไม่คืน body**
จึงไม่รั่วเนื้อหาหน้าเว็บออกไป · และยิง `example.com` ควบคู่เพื่อยืนยันว่าบริการยังทำงานปกติ

| path | ผลที่ได้จริง | ถ้า ACL ทำงานต้องได้ |
|---|---|---|
| `/dashboard/` | `200 OK` + `X-Served-By: ct109` (ของจริงจาก 109) | `403` |
| `/zzz-probe-12345` (ตกไป main forward) | `404 Not Found` (headscale) | `403` |
| `/api/nodes` | `401 Unauthorized` (headscale) | `403` |

**⚠️ ตารางนี้ยังใช้เป็นข้อสรุปไม่ได้** — ผลที่ได้ (`200`/`404`/`401`) อธิบายได้ด้วยเหตุ "source
อยู่ใน allow-list" ล้วน ๆ ไม่ได้พิสูจน์ว่า ACL ไม่ทำงาน

หลักฐานใหม่จาก log จริง (2026-10-02):

| อะไร | ค่า |
|---|---|
| client ที่ไม่ซ้ำกัน | `192.168.1.198` = **96,250** · `192.168.1.1` = 32 · public IP ~13 ราย **5–6 ครั้ง/ราย** (`203.144.248.35`, `171.97.169.166`, `110.78.5.54`, `209.15.97.185`, `202.139.215.30`, `49.237.86.86`, …) |
| `grep -c ' 403 403 '` | **0** → ไม่มีร่องรอยว่า ACL เคยปฏิเสธใคร |
| scheme | `http` 100 · `https` 96,505 (public IP อยู่บน `http` ทั้งหมด) |
| probe ของผม (10:22:16) | `[Client 192.168.1.198]` ← **อยู่ใน allow-list ⇒ ไม่ได้ทดสอบ ACL** |

⇒ public IP จริงมี แต่เป็น traffic **port 80** (`/ts2021` bootstrap) ซึ่งได้ **`301`** ไม่ใช่ `403`
· ส่วน 443 ถูก hubrelay relay เป็น `.198` ไปแล้ว (ดูด้านล่าง)

**`192.168.1.198` = hubrelay (CT 107) — ยืนยันโดยเจ้าของ** · และ router forward:
**external 443 → `192.168.1.198`** (hubrelay) → relay ต่อให้ NPM
⇒ NPM เห็น source ของ **external HTTPS ทุกครั้ง** เป็น `.198` ซึ่งอยู่ใน `allow 192.168.1.0/24`
**⇒ `deny all;` ไม่มีโอกาสทำงานกับ 443 เลย** — ไม่ใช่บั๊กของ config แต่เป็นเรื่อง ingress

หลักฐานประกอบ:
- `grep -c ' 403 403 '` = **0** · histogram: `[Client 192.168.1.198]` = 96,250
- non-LAN ที่โผล่มาเป็น **public IP จริง** มีแต่ `POST /ts2021` บน **`http` (port 80)** และได้
  **`301`** (http→https redirect) — **ไม่ใช่ `403`** · scheme ใน log: `http` 100 vs `https` 96,505
  ⇒ 443 แทบทั้งหมดเป็น `.198` · log format ยืนยัน `[Client $remote_addr]` (`conf.d/include/log-proxy.conf`)
- **เครื่องที่รัน agent ไม่ใช่ `.198`** (เจ้าของยืนยัน) → `.198` เป็น relay จริง

**ผลที่ตามมา:**
1. `/dashboard/` (ชื่อเครื่อง + IP + สถานะทั้งเครือข่าย) **internet อ่านได้** และไม่มี auth — จริง
2. ข้อสมมติของ ticket 02 ที่ว่า "ACL ที่ NPM ปิดช่องนั้นไปแล้ว" **ไม่จริง** — `/web/` และ `/api/*`
   ก็ไม่ได้ถูก ACL กันเช่นกัน (รอดเพราะ headscale มี auth ของตัวเอง ไม่ใช่เพราะ ACL)
3. ⚠️ **ห้าม "แก้ ACL" ด้วยการตัด hubrelay ออกหรือ bypass 443 ตรงไป NPM** โดยไม่ทดสอบ —
   Tailscale client นอกบ้าน connect `/ts2021` + `/derp` จาก **public IP ของ carrier** (ดูบรรทัด `/ts2021`
   ข้างบน) ซึ่งจะโดน `deny all` → **tailnet ล่ม** · ACL ชุดนี้ไม่เคยออกแบบมาสำหรับ host ที่เป็น
   control/DERP server

### สาเหตุ — สมมติฐานที่ตายแล้ว และตัวที่เหลือ

ตรวจเพิ่ม 2026-10-02:

| สมมติฐาน | วิธีตรวจ | ผล |
|---|---|---|
| (ก) ACL ไม่ได้อยู่ใน config ที่รัน | `nginx -T \| grep -nE 'allow 192\.168\.1\.0/24\|deny all;'` | ❌ ตาย — บรรทัด **1120/1123** (ระดับ `server{}`) และ **1126/1129** (`/web/`) อยู่ในรันจริง |
| ACL ไม่ได้อยู่ใน DB (แก้ไฟล์ตรง ๆ) | `grep -ac '...' /data/database.sqlite` | ❌ ตาย — เจอ 7 → อยู่ใน DB |
| (ข) vhost ซ้ำ `server_name` | `grep -rln 'arctictong-hs' /data/nginx/` | ❌ ตาย — มีไฟล์เดียว `proxy_host/12.conf` |
| (ค) custom include override | `cat /data/nginx/custom/server_proxy.conf` | ❌ ตาย — ไม่มีไฟล์/ว่าง |

⇒ ACL **อยู่ครบ ถูกที่ และ nginx รันอยู่จริง แต่ไม่มีโอกาสได้ทำงานกับ 443** — เพราะ ingress 443
ถูก hubrelay (`.198`) relay มาก่อน ⇒ NPM เห็น source เป็น `.198` ซึ่งอยู่ใน allow-list (public IP
จริงที่เหลืออยู่มีแต่ port 80 `/ts2021` ซึ่งได้ `301` ไม่ใช่ `403`)

**หลักฐาน:** บรรทัดที่อ่านด้วยตา — Chrome บน Windows, Chrome บน Android, `/derp` polling,
`python-httpx` และ **probe จาก hackertarget** — ล้วนเป็น `[Client 192.168.1.198]` (hubrelay)
⇒ `allow 192.168.1.0/24;` = "เปิดให้ทุกคน" โดยปริยายสำหรับ 443 · public IP ~13 รายใน histogram
คือ traffic **port 80** (`/ts2021` bootstrap) ซึ่งได้ `301` — ไม่ได้ถูก ACL ปฏิเสธเช่นกัน

⇒ **สรุป (2026-10-02): ACL ที่อิง `$remote_addr` ใช้ไม่ได้ในสถาปัตยกรรมนี้** เพราะ ingress 443
ถูก hubrelay (`.198`) relay หมด · กระทบ `/web/` + `/api/*` ที่พึ่ง ACL เดียวกันมาตั้งแต่ ticket 02

**ยืนยันแล้ว:** probe ของ hackertarget (10:22:16 UTC) ปรากฏเป็น `[Client 192.168.1.198]`
ทั้งสองบรรทัด (`/api/nodes` = `401` · `/zzz-probe-12345` = `404`) — และ `.198` = hubrelay CT 107
ที่ router forward 443 ไปหา (เจ้าของยืนยัน)

### หลักฐานการย้อนกลับ (2026-10-02) — วัดจาก NPM host

```txt
$ curl -s -o /dev/null -w '%{http_code}' https://arctictong-hs.duckdns.org/dashboard/
404                     # headscale ตอบ = ถอด Custom Location ออกจริง (เดิม 200 + X-Served-By: ct109)
$ curl -s -o /dev/null -w '%{http_code}' https://arctictong-hs.duckdns.org/web/
200                     # headscale UI ไม่กระทบ
$ curl -s -o /dev/null -w '%{http_code}' https://arctictong-hs.duckdns.org/api/nodes
401                     # headscale auth เดิม ไม่กระทบ
$ curl -s -o /dev/null -w '%{http_code}' http://192.168.1.200:9080/dashboard/
200                     # ในบ้านยังใช้ได้ครบ
```

⇒ **`/dashboard/` ไม่ถูกเปิดออก internet แล้ว** · ACL ยังกัน 443 ไม่ได้ (ดูหัวข้อสาเหตุ) แต่ไม่มีอะไรให้กันแล้ว
บน path นี้ · ที่เหลือคือ `/web/` + `/api/*` ซึ่ง headscale ป้องกันด้วย auth ของตัวเอง

### หมายเหตุที่ต้องเจอตอนทดสอบ

- `/dashboard` **ไม่มี** `/` ท้าย จะไม่ match location ใหม่ → ตกไป main forward → `404`
  (บน LAN ก็เป็นแบบเดียวกัน ต้องมี `/` ท้าย)
- `[Sent-to ...]` ใน access log ของ NPM **ไม่ใช่ upstream ของ location** — มันคือ `$server`
  ระดับ host (main forward) · บรรทัด `/dashboard/` จึงขึ้น `[Sent-to 192.168.1.197]` (headscale)
  ทั้งที่ของจริง 109 ตอบ → **อย่าใช้ `[Sent-to]` ตัดสิน routing ให้ใช้ `X-Served-By: ct109`**
- `[Length nnnn]` = ไบต์บนสาย (หลัง gzip) · nginx บน 109 gzip `/dashboard/` อยู่ (default ของ Debian)
  → `[Length 2622]` = หน้าจริง ~11 KB ที่ถูกบีบ ไม่ใช่หน้าถูกตัด · **อย่าเทียบกับ `Content-Length`
  ของ curl** (curl ไม่ส่ง `Accept-Encoding` จึงได้ 11229 ตัวเต็ม)
- **เครื่องที่รัน agent (opencode) อยู่ใน LAN** — `webfetch` ของ agent ยิง `http://192.168.1.200:9080/dashboard/`
  (ที่อยู่ LAN ล้วน) สำเร็จ → **ห้ามใช้ `webfetch` ตัดสินว่า "เปิดออก internet แล้วหรือยัง"**
- ⚠️ **แม้แต่ hackertarget ก็ยังไม่ใช่ vantage point ที่สะอาด** — probe มันเข้ามาเป็น `[Client 192.168.1.198]`
  (ไม่ใช่ public IP ของ hackertarget) ⇒ ต้องหา vantage ที่ **ไม่ถูก NAT/relay เป็น `.198`**
  หรือเลิกพึ่ง test ภายนอก แล้วดูจาก **จำนวน `403` ใน log** แทน
