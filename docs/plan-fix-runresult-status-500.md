# Rencana Perbaikan — 500 `ValidationError: RunResult.status = 'running'`

> **Dokumen rencana untuk agen eksekutor.** Berisi akar masalah (dengan `file:baris`),
> perbaikan bertahap, dan verifikasi. Audit pada branch `retrofit/chat-completions-adapter`
> (2026-08-03). Patuhi aturan global `docs/PROMPT_PLAN.md` §1.6 (tanpa mock, transparansi error).

---

## 1. Ringkasan

`POST /v1/agent/query` kadang membalas **HTTP 500** dengan pesan:
```
ValidationError: 1 validation error for RunResult
status  Input should be 'success','failed','exhausted' or 'timeout' [input_value='running']
```
Akar: `AgentLoop.run` bisa selesai dengan `state.status` masih `"running"`, lalu membangun
`RunResult(status="running")` yang **tidak valid** — dan konstruksi itu berada **di luar**
blok `try/except`, sehingga error tak tertangkap → 500. Perbaikan: **jamin `state.status`
selalu bernilai terminal sebelum `RunResult` dibangun**, plus jaring pengaman.

---

## 2. Gejala & Bukti

- Response 500 memuat `ValidationError ... RunResult.status ... input_value='running'`.
- Terjadi setelah run berjalan beberapa detik (bukan gagal di awal).
- Sering muncul saat LLM (deepseek/chat_completions) mengembalikan **respons kosong**
  (tanpa tool call & tanpa teks) di suatu iterasi.

---

## 3. Akar Masalah (kode)

Definisi tipe:
- `app/schemas/agent.py`: `RunResult.status: Literal["success","failed","exhausted","timeout"]`
  — **tidak** mengizinkan `"running"`.
- `app/schemas/agent.py`: `AgentState.status: Literal["running","success","failed","exhausted","timeout"]`
  — mengizinkan `"running"` (nilai awal).

Alur di `app/agent/loop.py`:
1. Bila respons LLM **tidak** punya `function_calls` **dan** tidak punya `output_text`, loop
   masuk cabang di **`loop.py:428-429`** (`# ... neither function calls nor output text` → `break`).
   Cabang ini **tidak menyetel status terminal** → `state.status` tetap `"running"`.
2. Finalizer fallback (**`loop.py:431-456`**) menghasilkan `final_answer` tetapi **juga tidak
   mengubah `state.status`**. (Lihat trace: `Fallback answer generated (status=running)`.)
3. `RunResult` dibangun di **`loop.py:523-529`** dengan `status=state.status` = `"running"`
   → `pydantic ValidationError`.
4. Baris 523 berada **di luar** `try/except` (try berakhir di `finally` **`loop.py:465`**),
   sehingga `except Exception` di **`loop.py:458`** **tidak menangkapnya** → error lolos ke
   FastAPI → **500**.

> Ini bug nyata, bukan salah input. Cabang "break to finalizer" adalah satu-satunya jalur yang
> meninggalkan status non-terminal; jalur lain (stop condition, final_answer, error) sudah
> menyetel status.

---

## 4. Pemicu Kontributor (bukan akar, tapi memperbesar peluang)

Body request memakai **`"store": true`** padahal LLM aktif adalah **deepseek/chat_completions**
(tak ada state server). Akibatnya planner (`app/agent/planner.py`, cabang `store=True`) hanya
mengirim `pending_inputs` tanpa riwayat lengkap → deepseek menerima urutan pesan janggal →
sering membalas **turn kosong** → memicu cabang §3.1.

Catatan penting: `opts.store` berasal dari `RunOptions.store` yang **default `True`**
(`app/schemas/agent.py`), sehingga `.env` `LLM_STORE=false` **tidak** merambat ke jalur API.
Hanya body request (atau perbaikan kode) yang bisa membuatnya `false`.

---

## 5. Rencana Perbaikan

### TASK 1 — Jamin `state.status` terminal sebelum `RunResult` (WAJIB, akar utama)
**File:** `app/agent/loop.py`
**Perubahan:** sebelum membangun `RunResult` (sekitar `loop.py:523`), pastikan status tidak
pernah `"running"`. Dua opsi (pilih salah satu, opsi A lebih bersih):

- **Opsi A — set status di finalizer.** Di blok finalizer (`loop.py:431-456`), setelah
  `final_answer` dihasilkan, tetapkan status terminal bila masih `"running"`:
  ```python
  if state.status == "running":
      # kita berhasil menyusun jawaban fallback meski model sempat mengembalikan turn kosong
      state.status = "success" if final_answer else "exhausted"
  ```
  Semantik: bila ada jawaban → `success` (dengan disclaimer yang sudah ada di prompt fallback);
  bila tidak → `exhausted`. Sesuaikan bila tim lebih suka menandai `exhausted` untuk kasus
  respons kosong.

- **Opsi B — koersi tepat sebelum `RunResult`.** Tambahkan guard defensif di `loop.py:522`:
  ```python
  if state.status not in ("success", "failed", "exhausted", "timeout"):
      state.status = "exhausted"
  ```

**DoD:** tidak ada jalur yang membangun `RunResult` dengan status `"running"`.

### TASK 2 — Tangani respons kosong secara eksplisit (mengurangi jalur "diam")
**File:** `app/agent/loop.py`
**Perubahan:** ganti `break` telanjang di `loop.py:429` agar mencatat kejadian dan menetapkan
niat finalisasi, mis. `tracer.event("thinking", "LLM mengembalikan respons kosong; finalisasi")`
lalu tetap `break`. (Status akhir tetap diatur oleh TASK 1.) Opsional: bila iterasi masih tersisa,
sisipkan satu reflection prompt sebelum menyerah, agar respons kosong tidak langsung mengakhiri run.

**DoD:** respons kosong menghasilkan trace yang jelas + jawaban fallback, bukan berhenti diam.

### TASK 3 — Jaring pengaman: `RunResult`/persistensi tak boleh pernah 500 (defensif)
**File:** `app/agent/loop.py`
**Masalah:** konstruksi `RunResult` (523) dan blok setelah `finally` berada **di luar**
`try/except`, jadi error apa pun di situ → 500 tak tertangkap.
**Perubahan:** bungkus konstruksi `RunResult` (dan idealnya seluruh ekor pasca-loop 469-529)
dalam penanganan yang mengembalikan `RunResult(status="failed", ...)` yang valid bila terjadi
error tak terduga — bukan melempar. Selaras dengan §1.6 (kegagalan → laporan jelas, bukan crash).

**DoD:** tidak ada exception dari ekor `run()` yang bisa lolos menjadi 500; kegagalan tak terduga
menjadi `RunResult(status="failed")`.

### TASK 4 — Selaraskan `store` dengan gaya API (menutup pemicu §4)
**File:** `app/agent/loop.py` (dan/atau `app/schemas/agent.py`)
**Perubahan:** saat `settings.LLM_API_STYLE == "chat_completions"`, **paksa** `opts.store = False`
(dengan log/trace penjelas), karena state server hanya berlaku untuk gaya `responses`. Ini
mencegah planner mengirim konteks janggal ke deepseek → mengurangi respons kosong di sumbernya.
Alternatif minimal: dokumentasikan bahwa body request untuk chat harus `store:false` (tapi
pemaksaan di kode lebih aman karena default `RunOptions.store=True`).

**DoD:** di mode chat, run tidak lagi mengandalkan `previous_response_id`; riwayat dikirim ulang;
frekuensi respons kosong turun.

> Urutan prioritas: **TASK 1** (hentikan 500) → **TASK 4** (tutup pemicu) → **TASK 3** (jaring
> pengaman) → **TASK 2** (kualitas). TASK 1 sendiri sudah menghilangkan 500 ini.

---

## 6. Strategi Pengujian (deterministik, tanpa mock jaringan — §1.6)

Uji **logika murni** dengan LLM client palsu-in-proc **hanya untuk orkestrasi** (bukan HTTP-mock):
inject sebuah `llm_client` stub ke `AgentLoop` yang mengembalikan `LLMResponse` dengan
`output_text=None` dan `function_calls=[]` (respons kosong).

- **T1 (akar):** jalankan `AgentLoop.run` dengan stub respons-kosong → **tidak** melempar
  `ValidationError`; mengembalikan `RunResult` dengan `status ∈ {success,exhausted}` dan
  `answer` tidak kosong.
- **T2 (status):** assert `state.status != "running"` di hasil.
- **T3 (jaring pengaman, opsional):** paksa error di ekor (mis. patch `RunResult`/persistensi)
  → `run()` tetap mengembalikan `RunResult(status="failed")`, bukan raise.
- **T4 (store chat):** dengan `LLM_API_STYLE=chat_completions`, `opts.store` efektif `False`.

Test live (marker `live`) opsional: kirim query nyata ke deepseek dengan `store:true` di body →
tetap mengembalikan RunResult valid (bukan 500).

---

## 7. Verifikasi Akhir

- [ ] Reproduksi lama (query "nuklir 5 hari", `store:true`, deepseek) → **bukan 500**; balas
      `RunResult` valid (200) meski model sempat mengembalikan turn kosong.
- [ ] `grep` memastikan tak ada jalur `RunResult(... status=state.status ...)` yang bisa
      menerima `"running"`.
- [ ] Ekor `run()` tidak dapat melempar exception tak tertangkap (TASK 3).
- [ ] Mode chat: `store` efektif `false` (TASK 4); respons kosong berkurang.
- [ ] Regresi: jalur normal (jawaban langsung, tool berantai, exhausted, timeout, failed) tetap
      mengembalikan status yang benar.
- [ ] Rahasia tetap teredaksi; tidak ada mock/fallback data palsu.

---

## 8. Risiko & Catatan

- **RS1 — Pemetaan status untuk respons kosong.** Memilih `success` vs `exhausted` untuk kasus
  turn kosong memengaruhi metrik. Rekomendasi: `success` bila ada jawaban fallback bermakna,
  `exhausted` bila tidak — konsisten dengan perilaku stop-condition yang sudah ada.
- **RS2 — TASK 4 mengubah perilaku store.** Memaksa `store=false` di chat adalah benar secara
  desain (`docs/plan-switch-deepseek-9router.md` §4), tapi pastikan planner jalur `store=false`
  mengirim riwayat yang cukup (uji multi-tool).
- **RS3 — Ekor di luar try/except** adalah sumber 500 lain di masa depan; TASK 3 menutupnya
  secara umum, bukan hanya untuk kasus ini.
- **Rujukan:** `docs/plan-fix-500-agent-query.md` (pola 500 sebelumnya & prinsip transparansi),
  `docs/plan-switch-deepseek-9router.md` (store untuk chat), `docs/PROMPT_PLAN.md` §1.3/§1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit kode, uji, commit) pada tahap terpisah.
