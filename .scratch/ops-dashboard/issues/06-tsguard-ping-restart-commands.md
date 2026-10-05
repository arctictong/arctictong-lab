# 06 — tsguard: `/ping <node>` + `/restart <node>` ผ่านแพทเทิร์น remote_commands เดิม

**Blocked by:** — (ขนานกับ 02–05 ได้เลย)
**Blocks:** —
**Labels:** seam-2, tdd

## ทำไม

Command center ของ spec: สั่งจาก Telegram ได้จบในแชทเดียวกับที่รับ alert
**ข่าวดี:** โค้ดมีโครงพร้อมอยู่แล้วมาก — `/status`, `/nodes`, `/history`, `/run`, `/confirm`, `/mute` ทำงานอยู่ (ดู `tsguard/src/tsguard/bot.py`), destructive command มีแพทเทิร์นยืนยันแบบ single-use message แล้ว, มี `remote_commands` allowlist + `exec` ที่ห้าม `shell=True`

## Context

- Repo: `F:\OpenCode\tsguard\` (Python 3.12, asyncio, uv, pytest — `uv run pytest`)
- อ่านก่อน: `tsguard/PLAN.md` (state machine, schema, ข้อจำกัด headscale 0.29) และ `docs/phase5-remote-commands.md`
- **Seam 2 ตกลงไว้ใน spec:** handler รับ `(command, args, context)` คืน message/keyboard — test ที่ชั้น handler ด้วย fake update, Telegram API เป็น edge บางๆ — โค้ดปัจจุบันแยกแบบนี้อยู่แล้ว (`test_bot.py`, `test_telegram.py`)
- `/restart` ที่ spec ค้างคำถามไว้ = "SSH เข้าจากไหน?" → คำตอบตามโครงที่มี: **ผ่าน `remote_commands` ที่ tsguard มีอยู่** (exec ผ่าน tailscale SSH allowlist) — ไม่เปิด SSH ใหม่เอง

## Scope

1. **`/ping <node>`** — รัน `tailscale ping <node>` บนตัว tsguard เอง (ผ่านกลไก exec ที่มี), ตอบ "pong Xms" หรือ timeout บน Telegram; จำกัด args ต้องเป็นชื่อ node ที่มีอยู่จริง (ดึงจาก store, ไม่รับ string มั่วไป exec)
2. **`/restart <node>`** — destructive: ต้องผ่าน `/confirm` ตามแพทเทิร์นเดิม (single-use message); จบด้วยผลลัพธ์จริง ถ้า exec ไม่สำเร็จต้อง **บอกว่าไม่สำเร็จพร้อมเหตุผล** ไม่หลอกว่าสำเร็จ
3. **Config:** เพิ่ม allowlist ใน `config.example.yaml` (เช่น alias `ping` → คำสั่ง local, `restart` → remote command ต่อ node ที่ permit), รักษากติกา `remote_commands.permits()`
4. **อัปเดต `/help`** — ครบคำสั่งใหม่
5. **Tests (Seam 2):** เพิ่มใน `tsguard/tests/test_bot.py` — fake update: มี node arg / ไม่มี arg / node ไม่มีอยู่ / restart ไม่ยืนยัน / ยืนยันแล้วสำเร็จ / exec fail แล้วตอบตามจริง

## Acceptance criteria (fail ได้จริง)

- [x] `uv run pytest` ผ่านทั้ง suite — **ก่อนเริ่ม พิมพ์ `/ping hubrelay` ใน Telegram แล้ว bot ตอบ unknown command**
- [x] `/ping hubrelay` → ตอบกลับ latency จริงใน Telegram; `/ping nonexist` → ตอบ error แบบอ่านรู้เรื่อง (ไม่ crash, ไม่ exec อะไร)
- [x] `/restart <node>` → ต้องขึ้นปุ่ม/ข้อความยืนยันก่อนเสมอ; กดยืนยันแล้วถึงรัน; ลอง restart แล้วตั้งใจให้ fail (node ปิด SSH) → ต้องรับข้อความความล้มเหลวจริง
- [x] `/help` โชว์คำสั่งใหม่
- [x] ไม่มี `shell=True` ถูกเพิ่มที่ไหนเลย (grep ต้องไม่เจอเพิ่มจากเดิม)

## ผลรัน (commit `ef68c43` บน `main` — ยังไม่ deploy)

- `/ping <node>` = alias `ping` ตั้ง `local: true` → argv `[wrapper, *args]` ไม่มี hop ผ่าน `tailscale ssh`; ping node ที่ offline ได้ (จุดประสงค์คือยืนยันว่าเครื่องตายจริง)
- `@node` sentinel (ใหม่ใน `config.py`): ชื่อ node ไดนามิกชิ้นเดียวที่อาจถึง argv ได้ — `RemoteExec.plan` แทนค่าให้เองตอนสร้าง argv และเฉพาะ alias ที่ประกาศไว้ใน `args_allowed`
- `/restart <node>` = resolve node แล้วเดินเส้นทาง `/run` ทั้งก้อน (allowlist → confirm token → audit); ไม่มี alias `restart` ใน config → ตอบ "ไม่อยู่ใน allowlist" พร้อมรายชื่อที่ใช้ได้
- `_arm_confirm` ถูกดึงออกมาเป็นจุดเดียว → alias `ping` ที่ตั้ง `danger: true` ก็ยังต้องขอ token (มี test ครอบ)
- เจอบั๊กเก่า 3 จุดที่ pre-existing ที่ `c7c502e` แล้วแก้ไปด้วย: `await self._later()` 4 จุด (`_later` เป็น sync → คำสั่งรันเสร็จแล้วยังขึ้น "ล้มเหลว" ปลอม, ปุ่ม run พังตาย), `_later` ซ้ำสองชุด, test wg-panel hardcode epoch แล้วแดงเองเมื่อผ่าน 10 นาที
- 614 passed, 5 skipped; `ruff check src tests` ผ่าน (repo ไม่ได้ใช้ `ruff format` เป็น gate)

### ที่ยังทำไม่ได้ / ต้องทำต่อบนเครื่องจริง

- **ยังไม่ deploy** — บนเครื่องนี้ `uv` ไม่มี และ `.venv` เดิมชี้ Python ของผู้ใช้อื่น (แก้ `pyvenv.cfg` + editable `.pth` ให้ชี้ `F:\OpenCode\tsguard\src` เพื่อรันเทสต์เท่านั้น ไม่ได้แตะของที่ deploy)
- **ยังไม่ได้พิสูจน์บน Telegram จริง** — ข้อความจริงว่า latency ออกมาเป็นอย่างไร ต้องรันหลัง deploy (`config.yaml` จริงต้องเพิ่ม alias `ping` และ `restart` เอง — ตัวอย่างอยู่ใน `config.example.yaml` แล้ว)
- wrapper `/usr/local/bin/tsguard-restart` ยังไม่มีไฟล์จริง ต้องเขียนเองก่อน `/restart` จะรันได้ (ยกตัวอย่างไว้ที่ `deploy/`)

## ไม่ทำ

- ไม่แตะ state machine / evaluator / notifier เดิม
- ไม่เพิ่ม `/status` ใหม่ (มีอยู่แล้ว), ไม่แตะ digest (มีอยู่แล้วใน notifier.py)
- ไม่ทำ `/restart` ผ่าน PVE API — ติดขัดตรงไหนให้เขียน note ไว้ใน ticket แล้วหยุด ไม่แก้ทางเอง
