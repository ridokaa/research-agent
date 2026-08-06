# [ARG-QUALITY] Plan — Perbaiki Over-constraint `platform` + Nonaktifkan Tool Mati `get_share_of_voice`

> **Dokumen rencana untuk agen eksekutor.** Tujuan: menghilangkan dua penyebab jawaban tipis yang
> **TERBUKTI deterministik-fixable** (bukan plafon model) dari probe langsung ke Verosight (2026-08-06):
> model men-set `platform='x'` tanpa diminta → `search_posts` kosong; dan `get_share_of_voice` selalu 400.
> Audit branch `retrofit/chat-completions-adapter` (2026-08-06). Patuhi `docs/PROMPT_PLAN.md` §1.6.
> **Disiplin anti-regresi: tiap tahap = test + commit sebelum tahap berikutnya** (baseline hijau `c5443a9`).

---

## 1. Bukti (probe langsung MCP Verosight, 2026-08-06)

Ditembak langsung ke tool MCP (jalur yang agen pakai), bukan lewat LLM:

| Tool | Panggilan | Hasil |
|---|---|---|
| `search_posts` | keyword=nuklir, tanggal recent, **TANPA `platform`**, sort=like_count | ✅ **DATA KAYA** — mengembalikan pemicu viral: *"LIVE PRESIDEN DI YOUTUBE KENA CUT... pidato Prabowo soal senjata nuklir Iran terputus... viral di X"* |
| `get_share_of_voice` | entities sebagai **list** | ❌ `HTTP 400 Bad Request` |
| `get_share_of_voice` | entities sebagai **comma-string** (seperti agen) | ❌ `HTTP 400 Bad Request` |

**Kesimpulan:**
1. `search_posts` **BEKERJA**. Di run agen ia kosong **hanya karena** model menyetel `platform='x'`
   (Twitter saja) + `sort='posted_at'` → menyaring habis. Pemicu viral SEBENARNYA tersedia (di TikTok, dll.).
2. `get_share_of_voice` **RUSAK** di sisi Verosight (400 apa pun format entities) → tool mati yang
   memboroskan panggilan & memicu error.

Kedua penyebab = **arg-quality / config**, bukan kecerdasan model. Deterministik-fixable, seperti date-fix.

---

## 2. Tahapan (SATU tahap sekali jalan; test + commit tiap tahap)

### Tahap 1 — Nonaktifkan `get_share_of_voice` (trivial, deterministik)
**File:** `.env` (atau default `config.py`) + verifikasi `app/tools/registry.py`.
- Tambahkan `verosight__get_share_of_voice` ke `TOOLS_DISABLED`. `_is_tool_active` (`registry.py:104`) sudah
  memfilternya di `get()`/`list_specs()`.
- **WAJIB verifikasi:** `to_tools()` (`registry.py:~201`) mengiterasi `_tools` langsung — **pastikan ia juga
  memanggil `_is_tool_active`** agar tool mati **tak ditawarkan** ke LLM (bukan hanya gagal saat eksekusi).
  Bila belum, tambahkan filter itu.
- **Catatan .env vs config:** `.env` gitignored (per-mesin). Untuk menonaktifkan permanen lintas-lingkungan,
  pertimbangkan default di `config.py` (ter-commit). Pilih salah satu; dokumentasikan.
- **Test (regresi):** unit test — dengan `TOOLS_DISABLED` memuat nama itu, `registry.list_specs()` **dan**
  `to_tools()` **tidak** memuat `verosight__get_share_of_voice`.
- **Gerbang uji:** run nyata → tool itu tak lagi dipilih/dipanggil; tak ada error 400 share_of_voice.
- **Revert-if-worse:** bila Verosight memperbaiki tool ini nanti → hapus dari `TOOLS_DISABLED`.

### Tahap 2a — Panduan prompt: jangan over-constrain `platform`/`sort` (primary, low-risk)
**File:** `app/agent/prompts/system.md`.
Tambah panduan (kondisional, bukan aturan kaku):
> "Untuk `search_posts`/tool berparameter `platform`: **JANGAN set `platform`** kecuali pengguna eksplisit
> menyebut platform (mis. 'di TikTok', 'di X'). Default: **kosongkan `platform`** agar mencakup semua
> platform. Untuk mencari **pemicu/konten viral**, gunakan `sort` berbasis engagement (mis. `like_count`),
> bukan `posted_at`."
- **Gerbang uji (≥3 run):** `search_posts` dipanggil **tanpa** `platform` (kecuali diminta) → `data` tidak
  kosong, memuat pemicu; jawaban lebih kaya (menyebut pemicu lintas-platform).
- **Revert-if-worse:** bila panduan membuat model bingung / mengabaikan platform yang diminta user → perlonggar.

### Tahap 2b — Backstop deterministik: normalisasi `platform` sadar-niat (opsional, bila 2a kurang)
**File:** `app/tools/executor.py` (+ threading `user_query` ke `execute()`).
- **Alasan:** 2a bergantung kepatuhan model (gpt-5.6-terra kadang abai). Backstop deterministik:
  bila arg `platform` di-set TAPI `user_query` **tak menyebut** platform mana pun → **kosongkan `platform`**.
- **Deteksi niat:** cocokkan `user_query` (lowercase) dengan kata platform: `tiktok`, `twitter`,
  `instagram`, `facebook`, `youtube`, `threads`, dst. **Caveat 'x':** huruf tunggal 'x' ambigu — deteksi
  hanya pola eksplisit ("di x", "platform x", "twitter/x"), jangan substring 'x' polos.
- **Plumbing:** `execute()` saat ini tak menerima `user_query` — perlu diteruskan dari loop (atau simpan di
  state yang dapat diakses executor). Terapkan mirip `_normalize_date_arguments`.
- **Test (regresi):** platform di-strip bila query tak menyebut platform; dipertahankan bila query menyebut
  (mis. "di TikTok" → platform tetap).
- **Gerbang uji:** query tanpa platform → arg `platform` kosong; query "di TikTok" → platform=tiktok dihormati.
- **Revert-if-worse:** bila salah strip platform yang diminta user → perketat/hapus.

---

## 3. Strategi Pengujian (§1.6 — output nyata, ≥3 run)

Set query campuran (termasuk "nuklir"):
- **(a) tanpa platform** ("apakah nuklir sering dibicarakan?") → `search_posts` lintas-platform, **memuat
  pemicu** (mis. pidato Prabowo di TikTok/X); jawaban menyebut substansi/pemicu — **tidak lagi tipis**.
- **(b) dengan platform** ("apa yang viral soal nuklir di TikTok?") → platform=tiktok **dihormati**.
- **(c)** `get_share_of_voice` **tak** ditawarkan/dipanggil; tak ada 400.
Metrik: proporsi `search_posts` kosong **turun**; pemicu muncul di jawaban; tak ada error tool mati.

---

## 4. Verifikasi Akhir

- [ ] `get_share_of_voice` tak ditawarkan ke LLM (`to_tools`) & tak dieksekusi; tak ada 400.
- [ ] `search_posts` dipanggil tanpa `platform` (kecuali diminta) → `data` tidak kosong, memuat pemicu.
- [ ] Query dengan platform eksplisit tetap dihormati.
- [ ] Test regresi hijau untuk kedua perilaku; suite penuh tetap **hijau**; `python -m py_compile` OK.
- [ ] Commit tiap tahap (tanpa push kecuali diminta).

---

## 5. Risiko & Batasan (jujur)

- **RS1 — Ambiguitas niat platform (seperti tanggal).** User mungkin memang mau satu platform. 2a menyerahkan
  keputusan ke model (dari query); 2b memakai kata-kunci query. **Blanket-strip `platform` selalu → SALAH**
  (merusak query platform sah). Jangan lakukan itu.
- **RS2 — Deteksi 'x' fuzzy.** Huruf tunggal 'x' ambigu; tangani hati-hati (pola eksplisit saja).
- **RS3 — Tool mati bisa hidup lagi.** Bila Verosight memperbaiki `get_share_of_voice`, cabut dari disabled.
- **RS4 — Sisa penyebab tipis (self-check longgar, sentimen tak dipanggil)** = finding terpisah (penilaian
  model / `finalize.md`), BUKAN cakupan plan ini. Lihat memori `agent-loop-reliability-state`.
- **Rujukan:** `docs/plan-fix-tanggal-otomatis-sistem.md` (pola normalisasi arg), `docs/plan-agen-lebih-cerdas.md`,
  `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit config/prompt/executor, test, commit) pada tahap
  terpisah dengan gerbang uji, aturan revert, dan **test + commit per tahap**.
