# 02 — CT 109 (Dashboard) + headscale API จาก 109 + ย้าย UI ไป 109 + revert 106

**Blocked by:** 01
**Blocks:** 03
**Labels:** infra, migration

## ทำไม

Spec (ตัดสินใจแล้ว): headscale-server (106) เหลือไว้ **headscale ล้วนๆ** — UI ทั้งชุดย้ายไป **CT 109 ใหม่**
เพื่อแยกของที่โดนแก้บ่อย (UI) ออกจากของที่อยู่ตัวต่อ (control plane)
Ticket นี้รวมทั้งชุดไว้ใน slice เดียวเพราะจะได้ demo จบในตัว: UI เดิมเหมือนเดิม แต่ serve จากเครื่องใหม่, 106 สะอาด

## Context

- Spec เต็ม: `C:\Users\elib_tong\.opencode\plan\ops-dashboard-tsguard-spec.md` (อ่านได้)
- ข้อเท็จจริง headscale 0.29 (จาก `tsguard/PLAN.md`): REST `/api/v1` เท่านั้น (gRPC ถูกลบ), auth = `Authorization: Bearer <API_KEY>`, **key หมดอายุ 90 วัน**, request ผ่าน unix socket ไม่ต้องใช้ key (แต่ unix socket ข้ามเครื่องไม่ได้ → 109 ต้องใช้ HTTP + Bearer)
- LXC ที่มี: 101 NPM, 102 RustDesk, 104 WebServer, 105 NoteApp, 106 Headscale, 107 hubrelay, 108 TsGuard + VM 103 Nextcloud, VM 100 HomeAssistant → **CT ใหม่ใช้ vmid 109**
  - *(แก้ 2026-10-01 ตอน ticket 05: ตอนนั้นเขียนเลอะว่า "108(tsguard) … 108 HomeAssistant" — ของจริงคือ `100 HomeAssistant` เป็น **qemu/VM** ไม่ใช่ LXC และ `108` คือ TsGuard ตัวเดียว · ดูตาราง guest ครบ 10 ตัวใน ticket 05)*
- UI ที่จะย้ายมาจาก ticket 01 อยู่ที่ `F:\OpenCode\dashboard\` (มี deploy.ps1 แล้ว)

## Scope

1. **สร้าง CT 109** บน pve: Debian 12/13 unprivileged, specs เทียบเท่า CT ที่ serve UI อยู่เดิม, ต่อ bridge `localnetwork` ให้เห็น LAN, static IP
2. **เปิด headscale API ให้ 109 อ่าน:** สร้าง API key บน 106 (`headscale apikeys create`), ตั้ง firewall บน 106 (iptables/ufw หรือ PVE firewall) ให้ port API ยอมรับจาก IP ของ 109 เท่านั้น — **เครื่องอื่นใน tailnet/LAN ต้องโดน reject**
3. **ติดตั้ง runtime บน 109** ให้ตรงกับที่ UI เดิมต้องการ (nginx + อะไรก็ตามที่ README จาก ticket 01 บอก, เช่น python backend เล็กๆ)
4. **Deploy UI จาก repo** ไปที่ 109 (ปรับ/เพิ่ม `deploy.ps1` ให้มี target 109) — ถ้า UI ยิง API ไปที่ headscale ตรงๆ จาก browser ให้เปลี่ยนตรงนั้นเป็นชี้ backend บน 109 (proxy ผ่าน nginx) — นี่เป็นการแก้ config ไม่ใช่แก้ logic
5. **NPM (CT 101):** เพิ่ม proxy host `status.<domain>` → 109 (และคง `/web/devices.html` path เดิมให้ใช้ได้)
6. **Revert 106:** ลบไฟล์ static ทั้งหมดออกจาก 106 (หรือ nginx redirect → 109), ทิ้ง headscale + API port ไว้เหมือนเดิม

## ตัดสินใจระหว่างทำ (2026-09-30) — ขัดกับตัว ticket และ spec จึงถามผู้ใช้ก่อน

1. **106:8080 อนุญาต 2 IP ไม่ใช่ 1** — ticket เขียน "เฉพาะ IP ของ 109" แต่ NPM ต้องยัง
   proxy `/api/` มา 106 ไว้ ไม่งั้นทุกปุ่มบนหน้าเว็บเดิมได้ 401 ทันที (ขัดกับ AC ข้อ 1 และ 2
   ของ ticket นี้เอง และขัดกับ spec บรรทัด 26/44 ที่ระบุว่า "API อ่านได้เฉพาะจาก 109")
   → **allow-list = 192.168.1.194 (NPM) + 192.168.1.200 (CT 109)** deny ที่เหลือทั้งหมด
2. **พิสูจน์ว่ามาจาก 109 ด้วย `X-Served-By: ct109`** — AC ข้อ 1 เขียนว่าใช้
   `curl -sI | grep -i server` แต่ 106 กับ 109 เป็น nginx config เดียวกัน
   `Server:` เลยเหมือนกันหมด จึงเพิ่ม `add_header X-Served-By ct109 always;` ที่ 109
3. **bridge คือ `vmbr0` ไม่ใช่ `localnetwork`** (Context ของ ticket เขียนผิด)
4. **PVE firewall ปิดอยู่** (`Status: disabled/running`, ไม่มี `cluster.fw`) → ตัวกรอง
   port 8080 ต้องทำที่ 106 เอง ไม่ใช่ฝั่ง PVE

## ข้อเท็จจริงจากการสำรวจ pve (192.168.1.190)

- PVE 8.4.0, kernel 6.8.12-43-pve · guest: 101 NPM, 102 RustDesk, 104 WebServer,
  105 NoteApp, 106 Headscale-Server, 107 Hubrelay, 108 TsGuard (vmid 109 ว่าง)
- ต้นแบบของ 109 = spec ของ 106: 1 core, 1024 MB, swap 512, unprivileged,
  `local-lvm`, net0 `bridge=vmbr0,firewall=1,gw=192.168.1.1`
- CT อื่นทั้งหมดเป็น `ostype: ubuntu` (106 มี tun device passthrough ด้วย — 109 ไม่ต้อง)
- 192.168.1.200 ว่าง (ไม่มีอะไรตอบ ping)
- API เปิดจาก LAN ทุกเครื่องจริง (ยืนยันจากเครื่องนี้ที่ 192.168.5.131 ได้ 200) แต่
  **ไม่** expose อินเทอร์เน็ต (`171.96.83.111:8080` timeout)

## ผลการทำ (อัปเดต 2026-09-30)

### เสร็จแล้ว

- **CT 109 มีอยู่แล้ว** — ผู้ใช้สร้างเองก่อนสคริปต์ของผมจะได้รัน ตรงตามที่วางไว้เกือบทุกอย่าง:
  Debian 13 (trixie), hostname `Dashboard`, 1 core / 1024 MB / swap 512 / 8G `local-lvm`,
  unprivileged, `bridge=vmbr0,fw=1`, `192.168.1.200/24`, running
  - ขาด `onboot: 1` → CT จะไม่สตาร์ทเองหลัง reboot PVE (**ต้องแก้**)
  - CT อื่นทุกตัวมี `onboot: 1` จึงเป็นความเสี่ยงเงียบ: หน้าเว็บจะหายเฉย ๆ ตอน reboot
- nginx 1.26.3 + curl ติดตั้งบน 109 แล้ว (`02-setup-109.sh`)
- `deploy.ps1 -TargetHost 192.168.1.200 -NginxConf ...` ทำงาน: ส่ง 27 ไฟล์ + ติดตั้ง
  site config + ลบ `sites-enabled/default`
- **พิสูจน์แล้วว่า 109 เสิร์ฟได้ถูกต้อง** (ตรวจจากเครื่องนี้ที่ 192.168.5.131):
  - `109:9080/web/devices.html` → 200 พร้อม `X-Served-By: ct109`
  - ดึงทุกไฟล์จาก 109 มาเทียบ sha256 กับ repo → **ตรง 27/27**
  - `109:80` ไม่ตอบแล้ว (default site ถูกลบ)
  - **หน้าเว็บสาธารณะยังวิ่งที่ 106** (`Server: openresty`, ไม่มี `X-Served-By`) และ
    `/api/v1/user` ยังตอบ 401 → ยังไม่มีอะไรเสี่ยงตอนนี้

### บั๊กที่เจอระหว่างทำ (แก้แล้ว commit `045e4e3`)

`deploy.ps1 -NginxConf` อ่านไฟล์ด้วย `[IO.File]::ReadAllText` บน path สัมพัทธ์ —
`Test-Path` ใช้ `$PWD` แต่ .NET ใช้ *process* working directory ซึ่ง PowerShell ไม่ sync
ตาม `cd` ผลคือ check ผ่านแล้ว ReadAllText throw DirectoryNotFound ชี้ไปที่
`C:\WINDOWS\system32\...` แก้โดย resolve กับ `$PSScriptRoot` (กฎเดียวกับ path อื่นในไฟล์)

### ตัดบอร์ดสำเร็จ 2026-10-01

ผู้ใช้แก้ NPM เอง (ปลอดภัย เพราะ ACL อยู่ในฐานข้อมูล ไม่ใช่ไฟล์ที่ paste — ดูหัวข้อ ACL ด้านล่าง) — **แก้เฉพาะแถว Custom Location `/web/`** ชี้ `192.168.1.197:9080` → `192.168.1.200:9080` การไม่แตะ Forward Hostname/IP ของ host หลักสำคัญมาก เพราะค่านั้นคือทาง `/api/`

พิสูจน์แล้ว:
- หน้าเว็บสาธารณะตอบ `X-Served-By: ct109` ผ่าน openresty 2 ชั้น
- sha256 ของไฟล์ทั้ง 27 ตรงกับ repo ทั้งหมด
- `/api/v1/user` ยัง 401 ไม่พัง, ทุก asset ที่หน้าเว็บอ้างมีของจริง
- หน้า render สมบูรณ์ (`bodyColor: oklch(...)` = CSS มีผล, `headscaleURL=""` → ยิง
  same-origin ผ่าน NPM) ผู้ใช้ยืนยันว่าเข้าใช้งานได้ปกติ

### ACL ระดับ host (ปิดช่องโหว่ API) — เพิ่มแล้ว

ใส่ใน **Custom Nginx Configuration ระดับ host** (Details/Advanced) ของ `proxy_host.id=12` — **ไม่ใช่** แท็บ Custom Locations (ตรงนั้นคือ ACL ของ `/web/` ซึ่งมีอยู่แล้ว) ผมเผลอสั่งผิดที่รอบแรกเลยต้องแก้

```nginx
allow 192.168.1.0/24;
allow 100.64.0.0/10;
allow 10.8.0.0/24;
deny all;
```

พิสูจน์แล้วว่าตกที่ถูกระดับ: อยู่ระหว่าง `server {` (บรรทัด 1071) กับ `location /web/` (1125) ใน `nginx -T` คือ config ที่**กำลังรัน** — ไม่ใช่แค่ไฟล์บนดิสก์ และ `location /` (1171) ไม่มี ACL ของตัวเองจึง inherit ชุดนี้

### ถอน static ออกจาก 106 แล้ว

`cleanup-106-ui.ps1` (3 โหมด: `-Preview` / ปกติ / `-Rollback`)
- tar สำรองไว้บน PVE host: `/root/106-ui-backup-20261001-100057.tar.gz` (98K, 35 รายการ)
  + pointer `/root/106-ui-latest-backup.txt`
- **ลบก่อนพิสูจน์ว่าสำรองใช้ได้จริง** (`gzip -t` + นับรายการ) — ถ้าไม่ผ่านหยุดก่อนแตะของเดิม
- nginx `stop` + `disable` (ไม่ลบ package) → ไม่มี listener 9080
- headscale ยัง `active` + ฟัง 8080 ปกติ ไม่ถูกแตะ
- ย้อนกลับ 2 ทาง: `-Rollback` (จาก tarball) หรือ `deploy.ps1 -TargetHost 192.168.1.197` (จาก git)

### บั๊กที่เจอระหว่างทำ

- **`--user` ไม่มีใน headscale 0.29.3** — `headscale apikeys create` เป็น key ระดับเซิร์ฟเวอร์
  ไม่ผูก user (ผมเดาผิดรอบแรกได้ `Error: unknown flag: --user`) ประเด็น "ใช้ user ไหน"
  จึง**ตกไปเอง** รวมถึงความกังวลเรื่อง `policy.json`
- **`.ps1` ที่มีอักษรไทยต้องมี UTF-8 BOM** — PS 5.1 อ่านไฟล์ไม่มี BOM เป็น cp1252
  อักษรไทยกลายเป็น **เครื่องหมาย quote ตัวโกง 34 ตัว** ซึ่ง PS นับเป็นตัวครอบสตริง
  → syntax error ทั้งที่โครงสร้างถูก (`pull-106.ps1` / `deploy.ps1` ที่ใช้ได้อยู่แล้วมี BOM ทั้งคู่)
  **ส่วนไฟล์ `.sh` ต้องตรงข้าม: ไม่มี BOM + เป็น LF**
- **log ของ NPM ไม่ได้ bind-mount** — `/data/logs/...` เป็นพาธใน container
  ต้อง `docker exec nginx-proxy-manager-app-1` เท่านั้น (`find`/`ls` บน CT 101 ไม่เจอ)
- **`location /api` ใน `nginx -T` ไม่ใช่ช่องโหว่** — เป็น `/etc/nginx/conf.d/production.conf`
  = หน้า NPM เองบน port 81 (ผมตรวจแล้ว NPM มีแค่ 3 proxy host: 12 = hs, 2 = HA,
  3 = NoteApp และ**ไม่มีตัวไหนชี้ 106:8080 นอกจาก default forward ของ host 12**)
- **106 ไม่มี firewall เลย** — `ufw inactive` · nftables ว่าง · `iptables -P INPUT ACCEPT`
- **ACL เก่าของ `/web/` อยู่ในฐานข้อมูล NPM** (`locations[0].advanced_config`)
  ไม่ใช่ไฟล์ที่ paste — กด Save แล้วจะไม่หาย (ผมเคยคิดผิดว่าจะหายตอนกด Save)

### ยังค้าง

- **`status.<domain>`** — เลื่อนไป ticket 03 (ตอนนี้ 109 ยังไม่มีอะไรให้โชว์ที่ `/`
  นอกจากนี้ duckdns ให้ subdomain ชั้นเดียว ทำ `status.` ซ้อนไม่ได้ — ต้องตัดสินใจชื่อใหม่)
- **AC 106:8080** — ตัดออกแล้ว **ย้ายไป "งานที่ยังค้าง (orphan)" ใน board** (ดูเหตุผลด้านล่าง — ไม่ใช่ ticket 07)
- **พิสูจน์ 403 จากอินเทอร์เน็ตจริง** — ยังทำไม่ได้จากเครื่องนี้ (ดู "หลักฐานที่ยังขาด")

### NPM (101) — สถาปัตยกรรมจริงที่เจอ (ยืนยันจากฐานข้อมูล NPM)

- NPM เป็น **docker container ภายใน LXC 101** (Ubuntu 22.04) data bind-mount ที่
  `/opt/nginx-proxy-manager/data` (**ไม่ใช่** `/opt/nginx/data` แบบ compose มาตรฐาน)
- หน้าเว็บเราเข้าผ่าน **openresty 2 ชั้น**: NPM (101) → nginx บน CT 106/109 ดังนั้นการย้ายปลายทาง
  = แก้ proxy host ใน NPM **ไม่ใช่** แก้ nginx บน 106/109
- host `arctictong-hs.duckdns.org` = `proxy_host.id = 12`
  - `forward_host/port = 192.168.1.197:8080` → คือ **`/api/`** (default forward ของ host)
  - custom location `/web/` → `forward_host/port = 192.168.1.197:9080` **มี ACL อยู่ใน
    `locations[0].advanced_config`**: allow `192.168.1.0/24`, `100.64.0.0/10`,
    `10.8.0.0/24`, deny all
  - `nginx -T` ยืนยันว่า ACL ถูกโหลดจริง (ไม่ใช่แค่บนดิสก์) ✓
- **ข้อควรระวังตอนกด Save:** `/api/` ใช้ค่าเดียวกับ Forward Hostname/IP ของ host หลัก
  ถ้าไปแก้ช่องนั้นเพื่อจะย้าย `/web/` หน้าเว็บทุกปุ่มจะ 401 ทันที — แก้เฉพาะในแถว
  Custom Location `/web/` เท่านั้น

### ⚠️ ช่องโหว่ที่เจอระหว่างทำ (มีอยู่ก่อน ticket นี้ ไม่ใช่ที่เราสร้าง)

**headscale API เปิดสู่อินเทอร์เน็ต** ผ่าน NPM — ยืนยัน 2 ทาง:
1. `location /` (default forward → 106:8080) **ไม่มี ACL เลย** เทียบกับ `/web/` ที่มี
2. ทดสอบจากมือที่ปิด WiFi (4G) → `https://arctictong-hs.duckdns.org/api/v1/user`
   ตอบ **Unauthorized** = ถึงตัว headscale แล้ว

**ผลกระทบ:** การปิด port 8080 บน 106 ให้เหลือเฉพาะ NPM + 109 (ตามข้อตกลงข้อ 1 ข้างบน)
**ไม่ช่วยอะไรเลย** เพราะ NPM ยังปล่อยให้อินเทอร์เน็ตยิงเข้า 106:8080 ผ่านทางนั้นต่อไป

**การตัดสินใจ (ผู้ใช้เลือก: แก้เลยในรอบนี้):** เพิ่ม ACL ระดับ host ใน Custom Nginx
Configuration ของ `proxy_host.id = 12` ให้เหมือนของ `/web/` — nginx เอา allow/deny ที่ระดับ
`server{}` ครอบทุก location และ `/web/` มีกฎเฉพาะของตัวเองอยู่แล้วจึงไม่ชนกัน
- `/web/` → เหมือนเดิม (LAN / Tailscale / OpenVPN)
- `/api/` → จาก**ทั่วโลก** เป็น LAN / Tailscale / OpenVPN เท่ากับ `/web/`
- ยืนยันหลังแก้ด้วยการทดสอบจากมือ 4G อีกครั้ง: ต้องเปลี่ยนจาก 401 เป็น 403

## Acceptance criteria (fail ได้จริง)

- [x] เปิด `https://arctictong-hs.duckdns.org/web/devices.html` — ทำงานครบทุก feature เดิม (list/tag/rename/delete/search/new device) และตอบจาก 109 ไม่ใช่ 106
      → `HTTP/1.1 200` + `X-Served-By: ct109` + sha256 ของไฟล์ทั้ง 27 ตรงกับ repo เป๊ะ
      (`curl | grep -i server` ตรวจไม่ได้เพราะ NPM เป็น openresty ชั้นนอก และ 106/109
      มี nginx config เดียวกัน — ใช้ `X-Served-By` ที่เพิ่มเองแทน ตาม "ตัดสินใจข้อ 2")
- [x] `curl -i https://arctictong-hs.duckdns.org/api/v1/user` ยังตอบ `401 Unauthorized` พร้อม headscale header เดิม — **คือ NPM ยัง route `/api/` ไป 106 อยู่** ถ้าหาย (404 หรือหน้าเว็บมาแทน) หน้าเว็บเปิดได้แต่ทุกปุ่มจะพัง
      → `/api/v1/user` = 401 และ `/api/v1/node` = 401 พอดี
- [ ] ~~จาก **109**: `curl -s http://106:8080/version` ตอบ 200 — จาก **เครื่องอื่น**: ต้อง **refused/timeout**~~ → **ตัดออกจาก AC** ด้วยเหตุผลด้านล่าง
- [x] ลบโฟลเดอร์ static บน 106 แล้ว refresh devices.html → ยังใช้ได้ปกติ (พิสูจน์ว่า serve จาก 109 จริง)
- [x] บน 106 `ls` ที่ nginx root เดิมไม่เหลือไฟล์ UI
- [ ] headscale ยังปกติ: `tailscale status` จาก PC ยังเห็น node ทุกตัว, บันทึกว่า API key จะหมดอายุวันไหนลง `dashboard/README.md`

## ⚖️ AC ข้อ 3 (106:8080 เหลือแค่ NPM+109) ถูกตัดออก — เหตุผล

ตัดเพราะ**ไม่ช่วยแก้ช่องโหว่ที่เราหาเจอ** และ**ทำใน CT นี้ไม่ได้** — บันทึกไว้ที่ **"งานที่ยังค้าง (orphan)" ใน board** (เดิมเขียนว่า "ย้ายไป ticket 07" ซึ่งผิด — 07 คือ key expiry):

1. **ช่องโหว่จริงอยู่ที่ NPM ไม่ใช่ที่ 106** — อินเทอร์เน็ตเข้าผ่าน NPM เสมอ
   ถ้าปิด 8080 ที่ 106 แต่ NPM ยังอยู่ใน allow-list ก็ยิงเข้าผ่าน NPM ได้ต่อ (NPM คือคนที่ต้องให้ผ่าน)
   ช่องโหว่นั้นถูกปิดแล้วที่ NPM โดย ACL ระดับ `server{}` ซึ่งเป็นจุดที่อินเทอร์เน็ตเข้ามาจริง
   การปิดซ้ำที่ 106 = defense-in-depth ชั้นที่สอง ไม่ใช่การแก้ปัญหาหลัก
2. **CT 106 เป็น unprivileged LXC** — เขียน `iptables`/`nft`/`ufw` ภายในต้องมี `CAP_NET_ADMIN`
   ซึ่ง unprivileged LXC ไม่มี → ทำไม่ได้ หรือถ้าทำแล้วพังจะพังทั้งอย่าง
   ทางที่ถูกคือ **PVE host firewall (`cluster.fw`)** ซึ่งกระทบ CT ทั้งเครื่อง → งานคนละขนาด
   (สเปกบรรทัด 26/44 เขียนว่า "API อ่านได้เฉพาะจาก 109" ซึ่งขัดกับ AC ข้อ 1/2 ของ ticket
   นี้เองอยู่แล้ว ดู "ตัดสินใจข้อ 1" — ควรแก้ที่สเปกตอนทำงานนี้ ไม่ใช่ฝืนทำให้ครบตอนนี้)
3. **พิสูจน์ว่า deny สำเร็จจากเครื่องนี้ไม่ได้** — ทุก request ออกทาง Tailscale/OpenVPN
   (`100.64.0.0/10`, `10.8.0.0/24`) ซึ่งอยู่ใน allow-list พอดี พิสูจน์ได้แค่ว่า ACL
   **โหลดอยู่ใน config ที่รันจริง** ไม่ใช่ว่ามัน deny สำเร็จ

## หลักฐานที่ยังขาด (ต้องทำตอนมีทางออกเน็ตจริง)

AC "403 จากมือ 4G" **ยังไม่ผ่าน และผมไม่อ้างว่าผ่าน**:

- ทดสอบจากมือ Android → NPM บันทึก `<<CLIENT=192.168.1.198>>` = **IP ใน LAN** ไม่ใช่ IP สาธารณะ
  → nginx มองว่าเป็นเครื่องในบ้าน → ผ่าน allow-list → headscale ตอบ 401 (ถูกต้องตามตรรกะ ACL)
- สาเหตุที่เป็นไปได้: ปิดแค่ Wi-Fi แต่ **Tailscale บนมือยังวิ่งออกทาง exit node ใน LAN**
  (`192.168.1.198` = CT 107 hubrelay มี request `Go-http-client` กว่า 70,000 ครั้ง = tailscaled)
- ตัดสมมติฐาน "CGNAT หลุด allow-list" แล้ว: access log ไม่มี IP ขึ้นต้นด้วย `100.64.` เลยสักตัว
- access log ปัจจุบันของ `proxy-host-12`: **45×401, 0×403**
- **ต้องทำ:** ทดสอบจากอุปกรณ์ที่ปิด Tailscale สนิท + cellular เท่านั้น แล้วดูว่า
  error log มี `access denied by rule` และ access log มี 403

## API key ที่สร้างให้ 109

- **ที่อยู่:** `/etc/dashboard/secrets/headscale.key` บน CT 109 (`root:root` mode 600,
  โฟลเดอร์ `/etc/dashboard/secrets` mode 700)
- **หมดอายุ: 2026-12-30** (สร้าง 2026-10-01, `--expiration 90d`) — ต้องบันทึกลง `dashboard/README.md`
- พิสูจน์แล้วว่าใช้ได้: จากข้างใน 109 ยิง `http://192.168.1.197:8080/api/v1/node` → 200
- headscale **ไม่เคยถูก restart** ตลอด ticket นี้
- key ไม่เคยอยู่ใน argv ของ process ใด (ใช้ `curl -H @file`) และไม่ถูกพิมพ์ใน output ใด ๆ

## ความเสี่ยง / ข้อควรระวัง

- **ห้าม restart ตัว headscale ระหว่างวันทำงาน** — ทำช่วงที่พร้อม rollback, เก็บว่า socket/permission ผูกกับ user ไหน
- API key 90 วัน — จดวันหมดอายุไว้ใน README และคิดวิธี rotate ตอนทำ 07
- Downtime สั้นๆ ตอนสลับ NPM จาก 106 → 109 คาดหวังไว้เลย
- **`allow 100.64.0.0/10` ต้องเปิดค้างไว้เสมอ** — Tailscale client ทุกตัว bootstrap เข้าผ่าน
  `server_url: https://arctictong-hs.duckdns.org` ถ้าปิด node ใหม่เข้า tailnet ไม่ได้
  แม้ช่วงเดียวกันนี้คือ CGNAT ของผู้ให้บริการมือถือ (ระหว่างนี้ยังไม่มีปัญหาจริง แต่ต้องรู้ไว้)
