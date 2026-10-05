# 05 — `/api/pve/summary` + panel Proxmox

**Blocked by:** 03
**Blocks:** —
**Labels:** seam-1, tdd

## ทำไม

Panel ที่สาม: เห็น CT/VM ทุกตัว, ว่ารันไหม, uptime, และ backup ล่าสุดโดนไหม — แทนการเปิดเว็บ PVE ทุกครั้ง
แยกจาก ticket 04 เพราะต้องตั้ง PVE API token ใหม่ (คนละแหล่งข้อมูลคนละ credential)

## Context

- Repo: `F:\OpenCode\dashboard\`, pattern เดียวกับ 03/04
- PVE API: host **`192.168.1.190`**, port 8006, node ชื่อ **`pve`** (single node), cert self-signed
  - **แก้จากเดิม:** ticket เขียน host `192.168.5.x` — ของจริงคือ `192.168.1.190`
- Auth แบบ **API Token**: user `dashboard@pve` + role `PVEAuditor`, token `dashboard@pve!dashboard` (`--privsep 0`)
  - **read-only เท่านั้น** — `PVEAuditor` ไม่มี `VM.PowerMgmt` อยู่ในชุดอำนาจเลย
  - secret อยู่ที่ `/etc/dashboard/secrets/pve.token` (mode 600) บน 109; token *id* (`dashboard@pve!dashboard`) อยู่ใน config เพราะไม่ใช่ความลับ
- Endpoint: `GET /api2/json/cluster/resources` (guests) · `GET /api2/json/cluster/tasks` (vzdump ล่าสุด) · `GET /api2/json/cluster/backup` (นับ job — best-effort)
- **`cluster/tasks` บน PVE ตัวนี้ไม่รับพารามิเตอร์ใด ๆ** — `limit`, `type`, `source` ล้วนตอบ `400 property is not defined in schema` → filter เป็นงานของเรา ไม่ใช่ของ server
- **`status == "OK"` เท่านั้นที่แปลว่าสำเร็จ** — task ที่ fail เอา *ข้อความ error* ใส่ `status` ตรง ๆ (`"failed to open /tmp/02-npm-db.py for reading"`) และ task ที่ยังรันอยู่จะไม่มีทั้ง `status` และ `endtime`
- **`cluster/resources` เป็น list ของทุกอย่างรวมกัน** (node + storage + guest) — guest คือแถวที่ `type` เป็น `lxc` หรือ `qemu`, ไม่ใช่ "ทุกแถว"
- `backup_recent` = มี vzdump ที่สำเร็จและจบภายใน 24 ชม. (`PVE_BACKUP_FRESH_HOURS`)
- Shape ตาม spec:
  ```jsonc
  { "guests": [ { "vmid": 105, "name": "NoteApp", "type": "lxc", "status": "running", "uptime_sec": 84100, "backup_recent": true } ] }
  ```

## ของจริงบนเครื่อง (2026-10-01 — เก็บเป็น fixture ในเทสแล้ว)

- **guests มี 10 ตัว ไม่ใช่ 9** (ticket เดิมเขียน "9 ตัว: 101–108 + 109" — ตก `100` ไป)
  `100 HomeAssistant` (**qemu**) · `101 NginxProxyManager` · `102 Rustdesk-server` · `103 Nextcloud` (**qemu**) · `104 WebServer` · `105 NoteApp` · `106 Headscale-Server` · `107 Hubrelay` · `108 TsGuard` · `109 Dashboard`
  ทั้งหมด `running`, node `pve`; เป็น lxc ยกเว้น 100 กับ 103 ที่เป็น qemu
- **ไม่มี backup job เลย** — `cluster/backup` = `[]` และใน 25 task ล่าสุดไม่มี `vzdump` สักตัว
  → `backup_recent` เป็น `false` ทั้ง 10 ตัว **ซึ่งเป็นคำตอบที่ถูก ไม่ใช่บั๊ก** หน้าเว็บจึงขึ้นหมายเหตุสีเหลืองอธิบายไว้ ไม่ปล่อยให้เป็นจุดแดง 10 จุดที่ไม่มีคำอธิบาย
  (การสร้าง backup job = งานของเจ้าของ ไม่อยู่ใน ticket นี้)
- `cluster/resources` คืน 14 แถว (10 guest + node + storage) — fixture เก็บเฉพาะ 10 guest

## Scope

1. **สร้าง PVE token ฝั่ง host** (manual step — ทำแล้ว): user `dashboard@pve` + role `PVEAuditor`, secret เก็บใน `/etc/dashboard/secrets`
2. **Endpoint `GET /api/pve/summary`** — ดึง resources + task, join หา backup ล่าสุดต่อ guest; `backup_recent` = มี vzdump สำเร็จใน 24 ชม.; TDD ด้วย payload จริงที่บันทึกไว้เป็น fixture
3. **Panel "Proxmox" บนหน้า `/`** — ตาราง: vmid, ชื่อ, ประเภท (LXC/VM), สถานะ, uptime อ่านง่าย, จุดสี backup (เขียว = backup มาล่าสุด / แดง = ไม่มีใน 24 ชม.)

## Acceptance criteria (fail ได้จริง)

- [x] `pytest` ผ่าน — case: guest running/stopped, มี/ไม่มี backup task — **ก่อนเริ่ม endpoint ไม่มีจริง (404)**
- [x] `curl -s http://109/api/pve/summary` = จำนวน guests ตรงกับเว็บ PVE (**10 ตัว: 100–109** — ไม่ใช่ 9) และ status ตรงกันทุกตัว
- [x] Token เป็น read-only: พยายาม `POST` start/stop ด้วย token นี้ → 403 จริง (บันทึกผลไว้ใน README)
- [x] หน้า `/` แสดงตาราง PVE ครบ, guest ที่กำลังหยุดอยู่ (ถ้ามี) ต้องไม่โกหกว่า running
- [x] CT ที่เพิ่ง backup สำเร็จ (เช่น ถ้า vzdump ล่าสุดของ 105 มาใน 24 ชม.) แสดงเขียว; ถ้าไม่มี backup เลย → แดง

## ไม่ทำ

- ห้ามมีปุ่ม start/stop/restart ใดๆ ใน panel (spec ตัดออก, รอบ 2 ค่อยว่ากัน)
- ไม่แตะ storage graphs / CPU graphs (ทำให้ยุ่งเกินจำเป็น)
- ไม่ pin certificate ของ PVE รอบนี้ (`PVE_VERIFY_TLS=false` เฉพาะ unit บน 109; default ในโค้ดเป็น `true`)
- ไม่สร้าง backup job แทนเจ้าของ

## หลักฐาน

### AC 1 — pytest + 404 ก่อนมี endpoint

Red ก่อน implement (ของจริง ไม่ใช่คำเล่า):

```
$ py -3.12 -c "...TestClient(APP).get('/api/pve/summary')..."
BEFORE: 404 application/json {"detail":"Not Found"}

$ py -3.12 -m pytest -q tests/test_pve.py tests/test_pve_api.py
ERROR collecting tests/test_pve.py
E   ModuleNotFoundError: No module named 'dashboard.collector.pve'
ERROR collecting tests/test_pve_api.py
E   ModuleNotFoundError: No module named 'dashboard.collector.pve'
```

Green หลัง implement:

```
$ py -3.12 -m pytest -q
134 passed in 11.08s
$ py -3.12 -m ruff check .
All checks passed!
```

(ก่อน ticket นี้ 102 tests → เพิ่ม 32 · 134 เป็นตัวเลขหลังแก้บั๊ก uptime ข้างล่าง)

### AC 3 — token เป็น read-only

```
POST https://192.168.1.190:8006/api2/json/nodes/pve/lxc/999/status/start
  -> 403 Permission check failed (/vms/999, VM.PowerMgmt)
```

### AC 2 — จำนวน guests ตรงกับ PVE (deploy จริงบน 109)

`http://192.168.1.200:9080/api/pve/summary` (ผ่าน nginx บน 109) เทียบกับ `cluster/resources` ที่ยิงด้วย token เดียวกัน:

```
PVE guests  : 10
API guests  : 10
backup_jobs : 0 | error: None
RESULT: MATCH
key set: ['backup_recent', 'name', 'status', 'type', 'uptime_sec', 'vmid']
  100 HomeAssistant      qemu running  backup=False
  101 NginxProxyManager  lxc  running  backup=False
  102 Rustdesk-server    lxc  running  backup=False
  103 Nextcloud          qemu running  backup=False
  104 WebServer          lxc  running  backup=False
  105 NoteApp            lxc  running  backup=False
  106 Headscale-Server   lxc  running  backup=False
  107 Hubrelay           lxc  running  backup=False
  108 TsGuard            lxc  running  backup=False
  109 Dashboard          lxc  running  backup=False
```

หน้า `/dashboard/` บน LAN → `200` (ตาราง Proxmox 10 แถว)

### AC 4 — guest ที่หยุดอยู่ไม่โกหก

`pct stop 105` แล้วยิง endpoint จริงบน 109:

```
[{'vmid': 105, 'name': 'NoteApp', 'type': 'lxc', 'status': 'stopped',
  'uptime_sec': None, 'backup_recent': False}]
```

`pct start 105` แล้วยิงซ้ำ:

```
[{'vmid': 105, 'name': 'NoteApp', 'type': 'lxc', 'status': 'running',
  'uptime_sec': 0, 'backup_recent': False}]
```

**นี่คือบั๊กที่เจอตอน deploy รอบแรก และเป็นบทเรียนเดียวกับ ticket 03 ซ้ำอีกรอบ**
รอบแรกคืน `uptime_sec: 0` ให้ CT ที่หยุดอยู่ → หน้าจะโชว์ `0s` (คำโกหก)
สาเหตุ: ผมเข้าใจว่า PVE ไม่ส่ง `uptime` มาให้ guest ที่หยุด — เทสผมเลยป้อน `{"status": "stopped"}` ที่ไม่มี key `uptime` แล้วผ่าน
ของจริงส่ง `"uptime": 0` มา
แก้ที่ `PveGuest._uptime_only_meaningful_while_running` (`790306c`) — uptime เป็น `None` ทุกสถานะที่ไม่ใช่ `running`
และเทสเปลี่ยนไปป้อน shape ที่เครื่องส่งจริง

### AC 5 — เขียวเมื่อมี backup ล่าสุด / แดงเมื่อไม่มี

- **แดง (พิสูจน์บนเครื่องจริง):** ไม่มี backup job เลย → ทั้ง 10 ตัว `backup_recent: false` → `down-bg` ทุกแถว
- **เขียว (พิสูจน์ด้วยเทส):** `test_a_successful_vzdump_makes_its_guest_recent` — vzdump สำเร็จ 1 ชม.ก่อน → `{105}`
  และอีกสามทิศทาง: เก่ากว่า window / `status` เป็นข้อความ error / ยังไม่จบ (ไม่มี `endtime`) → ไม่เขียว
- **ข้อจำกัดที่ต้องพูดตรง ๆ:** บนเครื่องไม่มี vzdump จริงให้เห็นแถวเขียวสด ๆ — จะเห็นได้ต่อเมื่อมี backup job ซึ่ง **งานนอก ticket นี้**

### เรื่องที่ต้องบอกเจ้าของ: PVE ไม่มี backup job

`GET /cluster/backup` = `[]` และใน 25 task ล่าสุดไม่มี `vzdump` สักตัว → เครื่องนี้ไม่เคย backup อะไรเลย
หน้าเว็บจึงขึ้นแถบเหลือง "No backup job is configured on PVE…" กำกับไว้ ไม่ปล่อยเป็นจุดแดง 10 จุดลอย ๆ
`backup_jobs` ถูกใส่ใน response เฉพาะเมื่อถาม PVE สำเร็จ — ถ้าถูกปฏิเสธจะไม่ใส่ (ไม่ใช่ `0`) เพื่อไม่ให้ "ไม่รู้" อ่านว่า "ไม่มี job"

### เรื่องที่ต้องบอกเจ้าของ: dashboard ยังไม่ออก public

`https://arctictong-hs.duckdns.org/api/*` และ `/dashboard/` ตอบจาก **headscale** ไม่ใช่ 109:

```
GET /api/nodes       -> 401 Unauthorized  (text/plain, 12 bytes, ไม่มี X-Served-By: ct109)
GET /dashboard/      -> 404 Not Found     (text/plain, 19 bytes = "404 page not found\n" ของ Go)
GET /web/            -> 200               (ถึง 109 จริง)
```

`/api/nodes` มีมาตั้งแต่ ticket 03 ก็ 401 เหมือนกัน → **พฤติกรรมเดิมของ NPM ไม่ใช่ regression ของ ticket 05**
และสอดคล้องกับ fact เดิมบน board ("NPM route `/api/` ไป headscale") → ตอนนั้น dashboard เข้าถึงได้เฉพาะ LAN
· **ต่อมา ticket 08 เปิด `/dashboard/` ให้เข้าจาก public host ได้แล้ว** (Custom Location แถวเดียว + คง ACL เดิม)
ส่วน `/api/*` ยังไม่เปิด เพราะหน้าเว็บไม่ต้องใช้และ `/api/` เป็นของ headscale
