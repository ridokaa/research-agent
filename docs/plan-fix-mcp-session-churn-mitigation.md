# Rencana Perbaikan — Mitigasi Client-Side MCP: Hentikan Churn Sesi & Timeout `get_topics`

> **Dokumen rencana untuk agen eksekutor.** Menekan drastis banjir `session_not_found` + kegagalan
> `get_topics` **tanpa** menunggu Verosight menyediakan Streamable HTTP. Menyerang **amplifier
> client-side**, bukan race SSE itu sendiri. Audit branch `retrofit/chat-completions-adapter`
> (2026-08-04). Patuhi `docs/PROMPT_PLAN.md` §1.6.

> **⚠️ KOREKSI PENTING (2026-08-05) — TASK 1 versi lama KELIRU.** Setelah TASK 1 lama diterapkan
> (menghentikan `close()`+reconnect saat error tool), hasil di Postman **memburuk**: `search_posts`
> & `get_topics` **gagal permanen** (tak ada retry pemulihan) → agen kehilangan data → jawaban
> buruk & topik meleset. **Pelajaran: pada Verosight SSE-only, `reconnect+retry` BUKAN sekadar
> churn — ia adalah MEKANISME RECOVERY** dari error transient SSE (attempt 1 gagal → attempt 2
> sukses). Menghapusnya menghilangkan pemulihan. TASK 1 ditulis ulang di bawah agar **mempertahankan
> recovery**; churn/noise diterima sebagai harga recovery. Fokus mitigasi aman = **TASK 2 (naikkan
> timeout)**, bukan mematikan retry.

---

## 1. Ringkasan

Log terbaru menunjukkan kondisi memburuk: `verosight__get_topics` **gagal** (timeout 30 dtk,
`mcp.call_tool_error … max_retries=3`), disertai **churn koneksi** (`client_closed`/`client_connected`
berulang, `sessionId` berbeda-beda tiap `session_not_found`). Akarnya **bukan** race SSE murni,
melainkan **amplifier di klien**: `call_tool()` menutup sesi (`self.close()`) pada **setiap**
exception → tiap error memaksa reconnect → tiap reconnect me-race `session_not_found` lagi. Dipicu
oleh tool analytics yang **lambat di server Verosight** (`get_topics`, `get_topic_sentiment`) yang
timeout di 30 dtk — **bukan** karena payload (terbukti: `limit:10` pun tetap timeout). Perbaikan ini
menghentikan cascade tersebut secara client-side.

> Catatan: race SSE **tidak bisa nol** tanpa Streamable HTTP (server-side, lihat
> `docs/catatan/verosight-mcp-sse-only.md`). Plan ini membuatnya **jarang & non-fatal**, bukan nol.

---

## 2. Gejala (log terbaru)

- `get_topics -> timeout … "timed out after 30.0s"` dengan `arguments={'keyword':'Prabowo','days':7,'limit':10}`
  → gagal **meski `limit:10`** (bukti bukan payload).
- `get_topic_sentiment -> timeout … duration_ms=57187` → tool analytics lain juga timeout (server lambat).
- `client_closed` → `client_connected` berulang; `session_not_found` dengan **sessionId berbeda**
  tiap kali → sesi baru dibuat berulang (churn) — churn ini **masih terjadi** (TASK 1 belum berlaku).
- Yang sudah membaik: `keyword:'Prabowo'` tampil apa adanya (fix over-redaction sudah jalan);
  `get_keyword_volume`/`get_sentiment` sukses.

---

## 3. Akar Masalah (kode)

`app/mcp/client.py`:
- `call_tool()` (baris ~215-260) & `list_tools()` (baris ~185-213) memakai loop retry yang, pada
  `except BaseException`, memanggil **`await self.close()`** (baris 254) lalu reconnect di attempt
  berikutnya. **Setiap** error (termasuk **timeout tool**) menghancurkan sesi.
- Panggilan `self._client.call_tool(..., timeout=self.timeout_s)` — `timeout_s` (per-call) = **30 dtk**.
- `get_topics`/`get_topic_sentiment` **lambat di sisi server Verosight**: bukti log 2026-08-04
  menunjukkan **tetap timeout >30 dtk meski `limit:10`** → jadi ini **kelambatan komputasi server**,
  **bukan** ukuran payload (mengecilkan `limit` terbukti TIDAK menolong).

Rantai: `get_topics` timeout (30s) → `close()` (sesi hancur) → reconnect → **re-race
`session_not_found`** (sessionId baru) → call lagi → timeout lagi → 3× (`max_retries=3`) → ~57-90 dtk
terbuang + banjir warning. **Timeout tool tidak seharusnya menghancurkan sesi** — koneksi/sesi masih
sehat; yang lambat hanya tool-nya.

> **Pembaruan 2026-08-04 (dari log terbaru):** (a) `get_topics`/`get_topic_sentiment` **masih**
> timeout walau `limit:10` → penyebab = **server lambat**, bukan payload. (b) Churn **masih**
> terjadi (`client_closed`→reconnect→re-race setelah timeout) → **TASK 1 belum berlaku**. (c) Level
> log `session_not_found` **dipertahankan `warning`** (keputusan pengguna: lebih jujur) → **TASK 5
> dibatalkan**.

---

## 4. Rencana Perbaikan

### TASK 1 — ~~Jangan tutup sesi saat error/timeout tool~~ → **PERTAHANKAN reconnect+retry (recovery)**
**File:** `app/mcp/client.py`
**Status:** versi lama TASK ini **KELIRU dan menyebabkan regresi** (lihat KOREKSI di atas). Bila
sudah diterapkan, **REVERT** ke perilaku reconnect+retry.
**Aturan baru (WAJIB dipatuhi):**
- **JANGAN menghapus `close()`+reconnect+retry pada error tool.** Pada Verosight SSE, error
  transient (`session_not_found` yang lolos ke level call, `ReadTimeout`, sesi terputus) **hanya
  pulih** lewat reconnect+retry (attempt 1 gagal → attempt 2 sukses). Menghilangkannya = tool gagal
  permanen → agen kehilangan data → jawaban buruk.
- **Boleh** menyempurnakan **hanya** kasus yang benar-benar aman & terbukti: mis. jangan reconnect
  untuk **timeout tool murni** (server lambat, koneksi sehat) **jika** dipastikan retry tak
  menolong — TETAPI ini **opsional & berisiko**; utamakan keamanan recovery. Bila ragu, **jangan
  utak-atik retry sama sekali**.
- Kurangi *dampak* churn lewat jalur lain (TASK 2/3), **bukan** dengan mematikan retry.
**DoD:** tool yang gagal karena error **transient** tetap **pulih** via retry (seperti sebelum
regresi); tidak ada tool yang gagal permanen akibat menghilangkan reconnect.
> **Pelajaran (2026-08-05):** di SSE-only, **churn = recovery**. Keduanya mekanisme yang sama; tak
> bisa dipisah. Jadi "menghentikan churn" **tidak boleh** berarti "mematikan retry".

### TASK 2 — Naikkan timeout untuk tool analytics yang lambat (bukan kecilkan payload)
**File:** timeout MCP di `app/mcp/*` / `app/config.py` (dan idealnya per-tool)
**Temuan menentukan (2026-08-04):** `get_topics` **tetap** timeout `>30 dtk` meski dipanggil dengan
`limit:10` → **kelambatan ada di server Verosight**, bukan ukuran payload. Karena itu:
- **Tuas utama — NAIKKAN timeout per-call** untuk tool analytics lambat (`get_topics`,
  `get_topic_sentiment`, `get_sentiment_trend`), mis. **30 → 60-90 dtk**. Idealnya **per-tool**
  (tool cepat tetap 30s; analytics lambat lebih besar), bukan global.
  - Perhatikan `AGENT_RUN_TIMEOUT_S` (180): jaga agar total tool serial tidak melampauinya.
- **Kecilkan payload BUKAN solusi di sini** — `limit:10` sudah dicoba dan tetap timeout. (Payload
  kecil tetap berguna untuk hemat konteks/biaya — rujuk `docs/plan-fix-truncated-answer-fallback-summary.md`
  — tapi **tidak** memperbaiki timeout ini.)
**DoD:** `get_topics`/`get_topic_sentiment` kembali `status=success` (bukan timeout) pada beban normal.

### TASK 3 — Sesi persisten (reuse) — **tanpa merusak recovery**
**File:** `app/mcp/client.py`, `app/mcp/manager.py`
**Perubahan:** usahakan satu sesi dibuat sekali dan **di-reuse** antar panggilan **sukses** (kurangi
re-init yang tak perlu). **Syarat:** reuse **tidak boleh** menghilangkan reconnect+retry saat error
transient (TASK 1). Bila reuse menyulitkan recovery, utamakan recovery.
**DoD:** lebih sedikit re-init pada panggilan sukses; recovery tetap utuh saat error transient.

### TASK 4 — ~~Kurangi amplifikasi retry~~ → **JANGAN kurangi retry (retry = recovery)**
**File:** `app/mcp/client.py`
**Status:** versi lama (kurangi retry menjadi 1×) **berbahaya** — sama seperti TASK 1, ia menghapus
pemulihan. Regresi 2026-08-05 mengonfirmasi: tanpa retry, tool transient **gagal permanen**.
**Aturan baru:** **PERTAHANKAN** retry+reconnect untuk error transient. Untuk mengurangi *jumlah*
percobaan, kecilkan **PEMICU**-nya lewat **TASK 2 (naikkan timeout)** — bukan mematikan retry.
**DoD:** tool transient tetap pulih; jumlah error berkurang karena timeout dinaikkan, bukan karena
retry dimatikan.

### TASK 5 — ~~Redam noise log (turunkan ke debug)~~ — **DIBATALKAN**
**Keputusan pengguna (2026-08-04):** `mcp.session_not_found_delay_detected` **tetap level `warning`**
(bukan `debug`). Alasan: warning lebih **jujur** — ia menandakan race SSE server yang nyata dan
berulang; menyembunyikannya sebagai debug membuat terminal tampak bersih padahal ada masalah
infrastruktur. Jangan turunkan level. (Bila sebelumnya sempat diturunkan ke debug, **kembalikan ke
`warning`**.)

> Prioritas (revisi 2026-08-05): **TASK 2 (naikkan timeout)** = tuas AMAN utama (kurangi *pemicu*
> error) → **TASK 3 (sesi persisten)** bila tak merusak recovery → **TASK 1 & 4 = PERTAHANKAN
> retry/recovery** (jangan matikan; itu penyebab regresi). Target realistis: **tool tetap pulih
> (jawaban baik)** sambil mengurangi *jumlah* error via timeout; sisa `session_not_found` **diterima
> sebagai harga recovery SSE** (level `warning`, jujur) sampai Verosight menyediakan Streamable HTTP.
> **Jangan** menukar kualitas jawaban demi log yang lebih bersih.

---

## 5. Strategi Pengujian (deterministik + live — §1.6)

- **T1 (RECOVERY — terpenting):** stub `_client.call_tool` yang **gagal di attempt 1 lalu sukses di
  attempt 2** → `MCPClient.call_tool` **tetap me-retry (reconnect)** dan mengembalikan **success**
  (bukan error). Ini menjaga agar regresi 2026-08-05 tak terulang.
- **T2:** stub yang melempar error **koneksi** → `close()`+reconnect+retry terjadi (jalur pemulihan sah).
- **T3 (live, marker `live`):** run multi-tool nyata → semua tool **akhirnya `status=success`**
  (pulih via retry); `get_topics` sukses (timeout dinaikkan). `session_not_found` **boleh** muncul
  (harga recovery SSE) — yang penting **tidak ada tool gagal permanen** dan jawaban baik.

---

## 6. Verifikasi Akhir (terikat ke log terbaru)

- [ ] **Kualitas jawaban TIDAK turun** — tool transient tetap **pulih** via retry; **tidak ada**
      tool gagal permanen (regresi 2026-08-05 tidak terulang). Ini kriteria lulus **utama**.
- [ ] `get_topics` kembali **sukses** (via TASK 2 naikkan timeout).
- [ ] Reconnect+retry **tetap** berfungsi saat error transient/koneksi putus (recovery utuh).
- [ ] Jumlah error **berkurang** karena timeout dinaikkan (TASK 2), **bukan** karena retry dimatikan.
- [ ] `session_not_found` sisa **boleh** ada (harga recovery SSE, level `warning`) — bukan kegagalan.
- [ ] Run tetap `200 OK` dengan jawaban baik; rahasia teredaksi.

---

## 7. Risiko & Catatan

- **RS1 — RETRY = RECOVERY (pelajaran regresi 2026-08-05).** Pada Verosight SSE, `reconnect+retry`
  adalah **satu-satunya** cara tool pulih dari error transient. **JANGAN** menghapus/mengurangi
  retry demi "menghentikan churn" — itu membuat tool gagal permanen → jawaban buruk (terbukti).
  Churn dan recovery adalah mekanisme yang sama; tak bisa dipisah. Uji T1 adalah pengaman utama.
- **RS2 — Timeout terlalu besar** (TASK 2) bisa menahan run lama saat server benar-benar hang;
  seimbangkan dengan `AGENT_RUN_TIMEOUT_S`.
- **RS3 — Kecilkan payload BUKAN solusi timeout ini** (terbukti `limit:10` tetap timeout). Fokus
  TASK 2 = **naikkan timeout per-tool**. Payload kecil tetap relevan untuk hemat konteks/biaya
  (rujuk `docs/plan-fix-truncated-answer-fallback-summary.md`), bukan untuk memperbaiki timeout.
- **RS4 — Ini mitigasi, bukan penyembuhan penuh.** `session_not_found` **akan tetap muncul** (harga
  recovery pada SSE) sampai Verosight menyediakan Streamable HTTP (`docs/catatan/verosight-mcp-sse-only.md`,
  `docs/MCP-CHANGE_migrasi-clientside-fastmcp.md` §4A). Targetnya **bukan** log nol, melainkan **tool
  tetap pulih & jawaban tetap baik** dengan noise seminimal mungkin lewat TASK 2 (naikkan timeout).
- **Rujukan:** `docs/catatan/verosight-mcp-sse-only.md`, `docs/MCP-CHANGE_migrasi-clientside-fastmcp.md`,
  `docs/plan-fix-truncated-answer-fallback-summary.md`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `app/mcp/*`, uji, commit) pada tahap terpisah.
