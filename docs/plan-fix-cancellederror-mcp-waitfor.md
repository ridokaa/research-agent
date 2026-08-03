# Rencana Perbaikan — `CancelledError` dari MCP/`asyncio.wait_for` → 500 "No response returned"

> **Dokumen rencana untuk agen eksekutor.** Menuntaskan bug 500 intermiten
> (`RuntimeError: No response returned`) yang berasal dari `asyncio.wait_for` di sekitar panggilan
> MCP (anyio). **Tambalan sebelumnya di `app/tools/executor.py` belum tuntas** — lihat §4.
> Audit pada branch `retrofit/chat-completions-adapter` (2026-08-03). Patuhi aturan global
> `docs/PROMPT_PLAN.md` §1.6 dan arsitektur **style-agnostic**.

---

## 1. Ringkasan

`POST /v1/agent/query` **sesekali** membalas HTTP 500 dengan
`"An unexpected server error occurred: RuntimeError: No response returned."`. Akar: sebuah
`asyncio.CancelledError` (turunan `BaseException`) lolos dari `ToolExecutor.execute` →
`execute_parallel` → `AgentLoop.run` → keluar sebagai artefak middleware Starlette
(`RuntimeError: No response returned`). Penyebabnya `asyncio.wait_for` (di
`app/tools/executor.py:215`) **tidak kompatibel** dengan SDK MCP berbasis **anyio**. Perbaikan:
hentikan pemakaian `wait_for` untuk panggilan anyio, pastikan `CancelledError` tidak pernah lolos
dari `execute()`, dan serialisasi panggilan pada satu sesi MCP.

---

## 2. Gejala & Bukti

- Response 500, `message` memuat `RuntimeError: No response returned` (di `APP_ENV=local`).
- **Intermiten** dan **cepat** (mis. ~7 detik — bukan timeout tool 30 detik), lebih mudah kambuh
  saat `parallel_tools=true` dengan beberapa tool MCP.
- Traceback (reproduksi): `asyncio.exceptions.CancelledError: Cancelled via cancel scope ... by
  Task ... ToolExecutor.execute() running at .../app/tools/executor.py:215`.
  Baris 215 = `raw_output = await asyncio.wait_for(tool.run(**args_dict), timeout=tool.spec.timeout_s)`.
- Rantai: `loop.py` → `executor.execute_parallel` (`asyncio.gather`) → `executor.execute`
  (`wait_for`) → `MCPTool.run` → `MCPClient.call_tool` (anyio) → `CancelledError` lolos.

---

## 3. Akar Masalah

**`asyncio.wait_for` + anyio tidak menyatu.** SDK MCP (`ClientSession`, transport SSE) memakai
**anyio cancel scope**. Saat `wait_for` timeout **atau** panggilan dibatalkan, mekanisme
`asyncio.wait_for` (di 3.11+/3.14 memakai `asyncio.timeout`) **membatalkan task `execute` itu
sendiri**. `CancelledError` yang muncul berasal dari *cancel scope* anyio — bukan dari mekanisme
`asyncio.timeout` — sehingga `wait_for` **tidak** mengubahnya menjadi `TimeoutError` dan
`CancelledError` bocor ke pemanggil.

**Pemicu cepat (intermiten):** `parallel_tools=true` menjalankan beberapa `call_tool` **bersamaan
pada satu sesi MCP** (`app/tools/executor.py::execute_parallel` → `asyncio.gather`). `ClientSession`
anyio tidak aman untuk akses konkuren pada sesi yang sama → tabrakan cancel-scope → salah satu
panggilan dibatalkan cepat → `CancelledError` di `wait_for`. (`asyncio.CancelledError` adalah
`BaseException`, jadi **tidak** tertangkap `except Exception` di `execute()` maupun di `loop.run`.)

---

## 4. Kenapa Tambalan Sebelumnya Belum Tuntas (penting)

`app/tools/executor.py` sudah memiliki handler:
```python
except asyncio.CancelledError:
    current = asyncio.current_task()
    if current is not None and current.cancelling() > 0:
        raise                     # <-- inilah yang menggagalkannya
    ... ubah jadi ToolResult(status="timeout") ...
```
Maksud guard `cancelling() > 0` adalah membedakan "pembatalan dari luar (shutdown/disconnect)"
dari "timeout `wait_for`". **Tapi** saat `wait_for`/anyio membatalkan task `execute`,
`cancelling()` menjadi `> 0` **juga** untuk kasus timeout/anyio ini. Karena `CancelledError`
bocor dari anyio (bukan dikonversi jadi `TimeoutError`), hitungan pembatalan **tidak** direset,
sehingga guard salah menyimpulkan "pembatalan eksternal" → **`raise`** → `CancelledError` lolos
lagi → 500. Guard ini harus diganti (lihat TASK 2).

---

## 5. Rencana Perbaikan

### TASK 1 — Ganti `asyncio.wait_for` dengan pola yang tidak membatalkan task pemanggil (akar)
**File:** `app/tools/executor.py` (sekitar baris 212-228)
**Perubahan:** ganti `asyncio.wait_for(tool.run(...), timeout=...)` dengan `asyncio.wait` +
pembatalan eksplisit, sehingga `CancelledError` tetap **terisolasi** di dalam task tool:
```python
task = asyncio.ensure_future(tool.run(**args_dict))
done, _pending = await asyncio.wait({task}, timeout=tool.spec.timeout_s)
if task in done:
    raw_output = task.result()          # bisa raise exc asli tool → ditangani except di bawah
else:
    task.cancel()
    try:
        await task                       # beri kesempatan cleanup di konteksnya sendiri
    except BaseException:
        pass
    # -> ToolResult(status="timeout", ...)
```
`asyncio.wait(timeout=...)` **tidak** membatalkan task pemanggil (berbeda dari `wait_for`),
sehingga pembatalan tool tidak merambat ke `execute`/loop. Pertahankan retry transient yang ada.
**DoD:** timeout tool menghasilkan `ToolResult(status="timeout")`; tidak ada `CancelledError`
yang keluar dari `execute()`.

### TASK 2 — Perbaiki handler `CancelledError` (jaring pengaman)
**File:** `app/tools/executor.py`
**Perubahan:** ganti guard `cancelling() > 0 → raise` dengan:
- panggil `asyncio.current_task().uncancel()` untuk **menyerap** pembatalan, lalu kembalikan
  `ToolResult(status="timeout")`;
- hanya `raise` bila ada sinyal shutdown yang jelas (mis. flag lifespan/AbortEvent), **bukan**
  sekadar `cancelling() > 0`.
Tujuan: `execute()` **tidak pernah** meloloskan `CancelledError` akibat timeout/anyio, tetapi
pembatalan shutdown yang sah tetap dihormati.
**DoD:** tidak ada `CancelledError` (BaseException) yang lolos dari `execute()` pada jalur timeout.

### TASK 3 — Serialisasi panggilan pada satu sesi MCP (hilangkan pemicu konkuren)
**File:** `app/mcp/client.py`
**Perubahan:** tambahkan `asyncio.Lock` per `MCPClient`; bungkus `call_tool` (dan `list_tools`)
dengan lock tersebut sehingga panggilan pada **sesi yang sama** berjalan berurutan, menghindari
tabrakan cancel-scope anyio. Tool **lokal** tetap boleh paralel; hanya panggilan ke satu sesi MCP
yang diserialisasi. (Alternatif lebih halus: pool koneksi MCP; tapi lock paling sederhana & aman.)
**DoD:** beberapa tool MCP dalam satu iterasi tidak lagi menabrak sesi; `execute_parallel` tetap
jalan (MCP berurutan di dalam, lokal paralel).

### TASK 4 — Hardening `execute_parallel` (defensif)
**File:** `app/tools/executor.py`
**Perubahan:** pertimbangkan `asyncio.gather(*tasks, return_exceptions=True)` lalu ubah setiap
hasil-exception menjadi `ToolResult(status="error")` sebelum dikembalikan — agar satu tool gagal
tidak pernah menjatuhkan seluruh batch. (Setelah TASK 1-2, `execute()` idealnya tak melempar;
ini lapisan tambahan.)
**DoD:** `execute_parallel` selalu mengembalikan `list[ToolResult]`, tak pernah melempar.

> Prioritas: **TASK 1 + TASK 3** (akar + pemicu) → **TASK 2** (jaring pengaman) → **TASK 4**
> (defensif). TASK 1+3 sudah cukup menghilangkan 500 ini.

---

## 6. Strategi Pengujian (deterministik, tanpa mock jaringan — §1.6)

- **T1 (timeout tool):** daftarkan tool lokal yang `await asyncio.sleep(10)` dengan
  `spec.timeout_s=0.2`. `execute()` mengembalikan `ToolResult(status="timeout")` dan **tidak**
  melempar. (Menguji TASK 1.)
- **T2 (CancelledError tak bocor):** tool yang melempar `asyncio.CancelledError` di tengah →
  `execute()` mengembalikan `ToolResult(timeout/error)`, tidak melempar; task pemanggil tidak
  ikut cancel. (Menguji TASK 1+2.)
- **T3 (paralel campur):** `execute_parallel` dengan 1 tool yang timeout + 2 tool sukses →
  hasilnya `[timeout, success, success]`, tidak ada exception yang lolos. (Menguji TASK 4.)
- **T4 (serialisasi MCP, live/marker `live`):** panggil 3 tool MCP dalam satu run `parallel_tools=
  true` berulang kali → tidak ada 500; lock memastikan panggilan sesi berurutan.

Reproduksi live (opsional): loop 5x query multi-tool (mis. "perang") `store:false,
parallel_tools:true` ke server persisten → nol 500.

---

## 7. Verifikasi Akhir

- [ ] Reproduksi lama (query multi-tool, `parallel_tools:true`) dijalankan berulang → **nol 500**.
- [ ] `grep` memastikan `asyncio.wait_for` tidak lagi membungkus `tool.run` di `executor.py`.
- [ ] `execute()` dan `execute_parallel()` tidak dapat meloloskan `CancelledError`/exception apa pun.
- [ ] Timeout tool tetap menghasilkan `ToolResult(status="timeout")` yang ditangani loop dengan mulus.
- [ ] Pembatalan shutdown/disconnect yang sah tetap menghentikan run (tidak tertelan).
- [ ] Panggilan MCP pada satu sesi berjalan berurutan (TASK 3); tool lokal tetap paralel.
- [ ] Regresi: jalur normal (tool sukses, empty, error, timeout) tetap benar; rahasia teredaksi.

---

## 8. Penutup Cepat Sementara (tanpa ubah kode)

Set **`"parallel_tools": false`** di body request Postman → tool dijalankan berurutan → tidak ada
tabrakan sesi MCP paralel → kemungkinan besar 500 ini hilang untuk sekarang (trade-off: sedikit
lebih lambat). Ini hanya penutup; perbaikan permanen ada di §5.

---

## 9. Risiko & Catatan

- **RS1 — Perilaku `asyncio.wait` vs `wait_for`.** Pastikan hasil/except tool tetap ditangani
  (`task.result()` bisa melempar exc asli tool → tangani seperti sebelumnya).
- **RS2 — Deteksi shutdown (TASK 2).** Bila tidak ada sinyal shutdown eksplisit, cukup selalu
  serap `CancelledError` jadi timeout di `execute()`; shutdown server tetap berjalan lewat jalur
  lifespan (bukan lewat pembatalan tool).
- **RS3 — Lock MCP (TASK 3)** menurunkan paralelisme MCP; dampak latensi kecil dibanding 500.
- **RS4 — Style-agnostic** tetap terjaga: semua perubahan di `app/tools/` & `app/mcp/`; loop tidak
  disentuh.
- **Rujukan:** `docs/plan-fix-500-agent-query.md` (pola 500 & transparansi),
  `docs/plan-fix-runresult-status-500.md` & `docs/plan-fix-empty-synthesis-and-fallback.md`
  (bug loop terkait), `docs/PROMPT_PLAN.md` §1.6.
- **Catatan sejarah:** tambalan awal (`except asyncio.CancelledError` dengan guard `cancelling()>0`)
  ada di `app/tools/executor.py` dan **harus diganti** oleh TASK 1-2; jangan hanya menumpuk di atasnya.
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode, uji, commit) pada tahap terpisah.
