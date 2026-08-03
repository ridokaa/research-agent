# Rencana — Beralih ke deepseek-v4-pro (9Router) dengan Metadata Verosight yang Sama

> **Dokumen rencana untuk agen eksekutor.** Tujuan: mengganti LLM dari OpenAI gpt-4o
> (gaya `responses`) ke **deepseek-v4-pro via 9Router** (gaya `chat_completions`), **tanpa**
> mengubah metadata tool Verosight dan **tanpa** menulis deskripsi tool sendiri. Audit pada branch
> `retrofit/chat-completions-adapter` (2026-08-03). Patuhi aturan global `docs/PROMPT_PLAN.md`
> §1.6 (tanpa mock, transparansi error). Arsitektur wajib tetap **style-agnostic**.

---

## 1. Ringkasan

Client project ini sudah mendukung **dua gaya API** di balik satu abstraksi (`LLMClient`):
`responses` (OpenAI) dan `chat_completions` (9Router/deepseek). Peralihan ke deepseek-v4-pro
**tidak** memerlukan penulisan ulang metadata tool maupun perubahan pada loop/planner/registry —
cukup **4 variabel koneksi di `.env`** plus **`LLM_STORE=false`**.

Poin kunci yang diminta pengguna: **metadata Verosight tetap identik dan tidak perlu dibuat
deskripsi sendiri.** Ini benar dan sudah terkonfirmasi di kode (lihat §3).

---

## 2. Konteks Saat Ini

`.env` aktif (OpenAI):
```dotenv
LLM_API_STYLE=responses
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-proj-...            # kunci OpenAI
LLM_MODEL=gpt-4o
LLM_STORE=true
VEROSIGHT_MODE=mcp                 # metadata resmi dari MCP list_tools()
```
Masalah yang mendorong peralihan: gpt-4o menabrak **rate limit (429)** di tier OpenAI saat run
otonom penuh (32 skema tool × banyak iterasi). deepseek via 9Router umumnya punya limit lebih
longgar → mendukung keinginan "agen memakai tool sendiri sesuai kebutuhan" tanpa 429.

---

## 3. Yang TETAP vs yang BERUBAH

### 3.1 TETAP (tidak disentuh)

- **Metadata Verosight (nama, deskripsi, parameter/schema).** Sumbernya `ToolSpec`, yang untuk
  `VEROSIGHT_MODE=mcp` diisi dari **metadata resmi `list_tools()`** (`app/mcp/adapter.py::mcp_tool_to_spec`).
  Registry menyediakan dua serializer yang membaca **spec yang sama**:
  - `app/tools/registry.py::to_responses_tools()` → FLAT `{"type":"function","name","description","parameters"}`
  - `app/tools/registry.py::to_chat_tools()` → NESTED `{"type":"function","function":{...}}`
  Perbedaannya **hanya pembungkus JSON**; isi `name`/`description`/`parameters` identik.
- **Pemilihan format otomatis.** `app/agent/loop.py` memanggil
  `registry.to_tools(api_style=settings.LLM_API_STYLE, ...)` → saat style `chat_completions`, otomatis
  memakai `to_chat_tools()`. Loop **tidak tahu** gaya mana yang dipakai.
- **Loop, planner, executor, registry, MCP, DB, tracing** — tidak ada perubahan kode.
- **`VEROSIGHT_MODE=mcp`**, daftar tool, `temperature`, `max_iterations`, `credits_budget`.

> **Kesimpulan:** pengguna **tidak** menulis deskripsi tool sendiri. Metadata resmi Verosight
> dipakai apa adanya untuk deepseek, sama seperti untuk OpenAI.

### 3.2 BERUBAH (hanya konfigurasi `.env`)

| Variabel | Dari (OpenAI) | Ke (deepseek/9Router) |
|---|---|---|
| `LLM_API_STYLE` | `responses` | `chat_completions` |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | `https://9router.fhs.my.id/v1` |
| `LLM_API_KEY` | kunci OpenAI | **kunci 9Router** (berbeda) |
| `LLM_MODEL` | `gpt-4o` | `xkiro/deepseek/deepseek-v4-pro` |
| `LLM_STORE` | `true` | **`false`** (lihat §4) |

Variabel yang menjadi no-op di gaya chat (aman, **tidak perlu dihapus**):
- `LLM_REASONING_EFFORT` → hanya untuk `responses`; diabaikan diam-diam oleh client di gaya chat.
- `LLM_MAX_OUTPUT_TOKENS` → dipetakan ke `max_tokens` bila dikirim; loop tidak mengirimnya, jadi
  tak berdampak.

---

## 4. Kenapa `LLM_STORE=false` WAJIB untuk deepseek

`LLM_STORE=true` + `previous_response_id` adalah **fitur khusus OpenAI Responses API** (state
percakapan disimpan di server OpenAI). Gaya `chat_completions` (9Router) **tidak punya** state
server — riwayat harus dikirim ulang tiap panggilan.

Alur di kode:
- `app/agent/loop.py` meneruskan `store=opts.store` (dari `settings.LLM_STORE`) ke
  `planner.build_request(...)`.
- `app/agent/planner.py`: bila `store=True` dan `last_response_id` ada → planner mengirim **hanya
  pending_inputs** (mengandalkan state server via `previous_response_id`).
- Di gaya chat, `previous_response_id` **diabaikan** dan tidak ada state server → bila store tetap
  `true`, model bisa **kehilangan konteks antar-iterasi**.

Karena itu set `LLM_STORE=false` agar planner memakai jalur "kirim ulang riwayat yang dipadatkan"
(sesuai desain `docs/PROMPT_PLAN.md` PROMPT 3 & 7). **Konsekuensi:** input token per panggilan naik
(riwayat dikirim ulang), tapi 9Router umumnya lebih longgar limitnya dibanding tier OpenAI.

---

## 5. Tahapan Implementasi

**Tahap 1 — Siapkan kredensial 9Router.**
Pastikan Anda punya kunci API 9Router yang valid (bukan kunci OpenAI). Simpan aman; jangan commit
`.env` (sudah gitignore).

**Tahap 2 — Ubah `.env`.**
Terapkan 5 perubahan pada §3.2. Contoh hasil akhir bagian LLM:
```dotenv
LLM_API_STYLE=chat_completions
LLM_BASE_URL=https://9router.fhs.my.id/v1
LLM_API_KEY=<KUNCI_9ROUTER_ANDA>
LLM_MODEL=xkiro/deepseek/deepseek-v4-pro
LLM_TEMPERATURE=0.2
LLM_MAX_OUTPUT_TOKENS=4096
LLM_TIMEOUT_S=60
LLM_STORE=false
LLM_REASONING_EFFORT=
VEROSIGHT_MODE=mcp
```

**Tahap 3 — Restart server** (config di-cache saat startup):
```bash
make dev
curl -s http://localhost:8000/health/deps    # llm harus configured (deepseek-v4-pro)
```

**Tahap 4 — Konfirmasi metadata tetap resmi & terformat benar.**
```bash
curl -s http://localhost:8000/v1/tools | jq 'length'          # jumlah tool sama seperti sebelumnya
python scripts/dump_mcp_tools.py                              # cek nama/desk/schema = metadata MCP resmi
```
(Deskripsi tool harus persis milik Verosight, bukan tulisan manual.)

---

## 6. Uji Live (WAJIB — dua hal yang khas gaya chat)

Ikuti aturan §1.6: tanpa mock; uji jalur nyata sebagai test live.

**U1 — Tool call ter-parse sebagai STRUKTUR, bukan teks.**
Risiko umum gateway: sebagian mengembalikan tool call sebagai teks biasa, bukan
`choices[0].message.tool_calls[]`. Client punya guard `response_parse`, tetapi deepseek-v4-pro via
9Router harus diverifikasi.
```bash
# Opsi A: lewat test live yang sudah ada (gaya chat)
pytest -m live tests/test_llm_live.py -k "tool_call or round_trip" -q
# Opsi B: lewat API end-to-end
# POST /v1/agent/query {"query":"Apa yang sedang tren di Indonesia sekarang?","options":{"max_iterations":4}}
```
Lalu buka trace: `GET /v1/runs/{run_id}/trace?format=text` → pastikan ada event `tool_selected`
dengan `tool_calls` ter-parse (bukan jawaban teks yang menyebut nama tool).

**U2 — Konteks antar-iterasi utuh (dengan `LLM_STORE=false`).**
Jalankan query yang memicu beberapa tool berantai; verifikasi di trace bahwa iterasi ke-2+
memakai hasil iterasi sebelumnya (bukan mengulang dari nol). Bila konteks hilang → cek ulang
`LLM_STORE=false` benar terbaca.

**U3 — Error transparan.**
Kunci 9Router salah → `POST /v1/agent/query` membalas `status:"failed"` dengan
`ErrorReport(component="llm", stage="auth")` (bukan 500 tersamar).

---

## 7. Verifikasi Akhir

- [ ] `health/deps` → `llm` configured dengan `xkiro/deepseek/deepseek-v4-pro`.
- [ ] `GET /v1/tools` → jumlah & isi metadata **sama** seperti saat OpenAI (nama/desk/schema resmi Verosight).
- [ ] Trace menunjukkan `tool_selected` dengan tool call ter-parse (U1 lulus).
- [ ] Query multi-tool: konteks antar-iterasi terjaga (U2 lulus).
- [ ] `LLM_STORE=false`; tidak ada `previous_response_id` yang dikirim di gaya chat.
- [ ] Run otonom penuh (tanpa `tools_allow`) berjalan **tanpa 429** (tujuan awal peralihan).
- [ ] Kunci salah → `ErrorReport(stage="auth")`, bukan crash/mock (U3 lulus).
- [ ] Rahasia (`api_key`) tetap teredaksi di log/trace/response.
- [ ] Regresi: bila dikembalikan ke OpenAI (`responses`, store=true), tetap berfungsi.

---

## 8. Risiko

- **RS1 — Gateway mengembalikan tool call sebagai teks.** Bila U1 gagal, agen tak akan memanggil
  tool. Mitigasi: verifikasi dukungan function-calling deepseek-v4-pro di 9Router; bila perlu,
  pilih model 9Router lain yang mendukung tool calls terstruktur.
- **RS2 — Lupa `LLM_STORE=false`.** Konteks antar-iterasi hilang → jawaban dangkal/berulang.
  Mitigasi: item checklist §7.
- **RS3 — Input token lebih besar** karena riwayat dikirim ulang tiap panggilan (konsekuensi
  store=false). Mitigasi: 9Router umumnya lebih longgar; pantau biaya; `max_iterations` wajar.
- **RS4 — Kredensial 9Router** kedaluwarsa/kuota habis → `stage="auth"`/`rate_limited`. Mitigasi:
  cek saldo/limit akun 9Router; error muncul transparan lewat `ErrorReport`.
- **RS5 — Perbedaan perilaku model** (deepseek vs gpt-4o) pada pemilihan tool/penyaringan lokasi.
  Bila penyaringan Indonesia perlu diperkuat, rujuk `docs/plan-percobaan-model-berbeda.md` Tahap B
  (perkuat `system.md`) — berlaku lintas model.

---

## 9. Catatan & Batasan

- **Tidak menulis metadata sendiri.** Deskripsi/schema tool tetap dari `list_tools()` Verosight;
  peralihan model tidak menyentuhnya. Ini konsisten dengan `docs/plan-perubahan-cara-kerja.md`
  (satu sumber metadata resmi).
- **Style-agnostic dijaga.** Semua perbedaan gaya diserap di `app/llm/`; tidak ada `if style==...`
  di `app/agent/` atau `app/tools/`.
- **Peralihan reversible via konfigurasi.** Kembali ke OpenAI cukup mengembalikan 5 variabel §3.2
  (termasuk `LLM_STORE=true`). Tidak ada perubahan kode yang perlu di-rollback.
- **Rujukan silang:** `docs/PROMPT_PLAN.md` (PROMPT 3 dua-gaya, §1.6); `docs/Plan-perubahan-model.md`
  (sinkronisasi konfigurasi model); `docs/plan-fix-500-agent-query.md` (bila 500 muncul);
  `docs/plan-percobaan-model-berbeda.md` (penyaringan per-lokasi & penguatan prompt).
- **Batasan tugas:** dokumen rencana; eksekusi (ubah `.env`, restart, uji, commit) dilakukan pada
  tahap terpisah oleh agen/developer.
```
