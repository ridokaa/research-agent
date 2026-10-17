# [DATE-AWARENESS] Plan — Sistem Menentukan & Menghitung Tanggal Sendiri (model-independent)

> **Dokumen rencana untuk agen eksekutor (bukan implementasi kode).** Tujuan: **sistem** yang menentukan
> tanggal hari ini (dari `datetime.now()`) **dan** menghitung rentang tanggal tool secara **deterministik &
> benar** — tak bergantung pada "jam internal" model (yang rapuh: deepseek kebetulan akurat, gpt-5.6-terra
> meleset ~5 bulan → `search_posts` kosong). Audit branch `retrofit/chat-completions-adapter` (2026-08-06).
> Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Akar Masalah (TERBUKTI)

Run gpt-5.6-terra (`7b11d4fa`, "nuklir", 2026-08-06): `search_posts` dipanggil dengan
`date_from='2026-02-24', date_to='2026-03-26'` (~5 bulan lalu) → hasil **`data=[]`** → tak ada pemicu/topik
→ jawaban tipis.

**Penyebab:** sistem **tidak** menyuntikkan tanggal hari ini (terverifikasi: `system.md` tak memuat tanggal).
Model menebak tanggal dari jam internalnya. deepseek kebetulan ~akurat; gpt-5.6-terra menebak ~Maret 2026.
Tool relatif (`get_volume days=30`) benar karena **server Verosight** yang menghitung; hanya tool bertanggal
**absolut** (`search_posts`) yang meleset. → Mengandalkan jam internal model **rapuh & beda per model**.

**Prinsip perbaikan:** jangan serahkan perhitungan tanggal ke model. **Sistem yang menghitung**, deterministik,
dari `datetime.now()`. (Sudah terbukti benar untuk tool relatif; terapkan prinsip sama ke tool absolut.)

---

## 2. Desain (Cara #2 inti + Cara #1 pendukung)

**Cara #2 — Normalisasi tanggal di lapisan tool (INTI, deterministik).**
Di `app/tools/executor.py`, method `execute()`, **setelah** argumen diparse jadi `args_dict` (~baris 170)
dan **sebelum** validasi schema (baris 174), sisipkan:
```python
args_dict = self._normalize_date_arguments(tool, args_dict)
```
Fungsi `_normalize_date_arguments(tool, args)`:
- **Deteksi schema-driven:** hanya bertindak bila `tool.spec.parameters` punya properti tanggal
  (`date_from`/`date_to`) — jadi otomatis berlaku untuk tool bertanggal apa pun, kini & nanti.
- **`today = datetime.now(TZ).date()`** (lihat catatan zona waktu di bawah).
- **Aturan (deterministik):**
  1. Bila `date_from`/`date_to` **kosong** → isi default recent: `date_to=today`,
     `date_from=today - DEFAULT_WINDOW_DAYS` (mis. 30). (Selaras perilaku tool relatif yang sudah benar.)
  2. Bila `date_to` **di masa depan** (> today) → clamp ke `today`.
  3. Bila `date_from > date_to` → perbaiki (swap / set `date_from=date_to - DEFAULT_WINDOW_DAYS`).
  4. Format `YYYY-MM-DD` konsisten.
- **Deterministik & selalu benar** karena dihitung Python, bukan model.

**Cara #1 — Suntik tanggal ke prompt (PENDUKUNG, dinamis).**
Di `app/agent/planner.py` `build_system_prompt` (yang **sudah** `.format(...)` dinamis tiap request),
tambah `current_date=datetime.now(TZ).strftime("%A, %d %B %Y")` dan placeholder `{current_date}` di
`system.md`. Tambah panduan singkat di `system.md`:
> "Hari ini {current_date}. Untuk pertanyaan 'akhir-akhir ini/terkini', gunakan parameter relatif `days`
> atau **kosongkan** `date_from`/`date_to` (sistem akan mengisi rentang terkini). Set tanggal absolut
> HANYA bila pengguna meminta periode historis spesifik."

Efek gabungan:
- Pertanyaan "terkini" → model mengosongkan tanggal → **Cara #2 mengisi recent (benar)**.
- Pertanyaan historis spesifik ("Maret lalu") → model set tanggal → dihormati (valid, bukan masa depan).
- Model punya **konteks** waktu untuk penalaran (Cara #1), tapi **kebenaran dijamin kode** (Cara #2).

---

## 3. Catatan Zona Waktu (WAJIB diputuskan)

Data Verosight = media sosial Indonesia (WIB, UTC+7). "Hari ini" harus WIB agar batas tanggal benar.
Rekomendasi: `TZ = ZoneInfo("Asia/Jakarta")` (via `zoneinfo`), atau setelan `AGENT_TIMEZONE` di `config.py`
(default `Asia/Jakarta`). **Jangan** pakai UTC polos (bisa geser 1 hari di dini hari WIB).

---

## 4. Tahapan (SATU tahap sekali jalan)

### Tahap 1 — Cara #2: `_normalize_date_arguments` (INTI)
**File:** `app/tools/executor.py` (+ `config.py` untuk `AGENT_TIMEZONE`/`DEFAULT_WINDOW_DAYS` opsional).
Implementasi §2 Cara #2. **Gerbang uji (≥3 run):**
- `search_posts` untuk query "terkini" → argumen tanggal **recent** (mendekati today) → `data` **tidak
  kosong** → pemicu/topik muncul → jawaban lebih kaya.
- Log `Executed tool 'verosight__search_posts'` menunjukkan `date_from`/`date_to` terkini.
- Tak ada tanggal masa depan; tak ada regresi pada tool relatif (`get_volume days=30` tetap jalan).
**Revert-if-worse:** bila query historis jadi salah dipaksa recent, atau muncul error validasi tanggal → revert.

### Tahap 2 — Cara #1: suntik `current_date` + panduan prompt
**File:** `app/agent/planner.py`, `app/agent/prompts/system.md`.
Implementasi §2 Cara #1. **Gerbang uji (≥3 run):** model lebih sering mengosongkan tanggal untuk query
terkini (membiarkan sistem mengisi); jawaban menyebut konteks waktu yang benar.
**Revert-if-worse:** bila panduan membuat model bingung/under-call → perlonggar teks.

---

## 5. Verifikasi Akhir

- [x] `search_posts` (query terkini) → tanggal recent otomatis → `data` tidak kosong → pemicu ketemu.
- [x] Tanggal dihitung **sistem** (log memperlihatkan rentang benar) — bukan tebakan model.
- [x] Query historis spesifik tetap dihormati (tak dipaksa recent).
- [x] Zona waktu Asia/Jakarta (batas tanggal benar di dini hari WIB).
- [x] Tool relatif (`days`) tetap jalan; tak ada regresi; tak ada tanggal masa depan.
- [x] `python -m py_compile` OK; rahasia teredaksi.

---

## 6. Risiko & Batasan (jujur)

- **RS1 — Ambiguitas "terkini" vs "historis".** Lapisan tool tak tahu niat query. Kebijakan default =
  "isi recent bila tanggal kosong; hormati bila diisi". Kasus model **memaksa** tanggal lama untuk query
  terkini (seperti gpt-5.6-terra) hanya tertangani bila Cara #1 berhasil menyetir model mengosongkan
  tanggal. Bila model tetap memaksa tanggal lama → di luar jangkauan #2 murni (butuh niat query). Pantau.
- **RS2 — Zona waktu.** Salah TZ → geser 1 hari. Pakai Asia/Jakarta.
- **RS3 — `DEFAULT_WINDOW_DAYS`.** Default 30 hari; bila terlalu sempit/lebar untuk sebagian query →
  jadikan setelan.
- **RS4 — Ini bukan soal model.** Berlaku untuk deepseek, gpt-5.6-terra, model apa pun (justru itu tujuannya:
  konsisten benar lintas model). Lihat memori proyek `router-404-model-not-found-transient`,
  `agent-loop-reliability-state`.
- **Rujukan:** `docs/MODEL-CHANGE_gpt-5.6-terra.md`, `docs/plan-agen-lebih-cerdas.md`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `executor.py`/`planner.py`/`system.md`, uji ≥3 run,
  commit) pada tahap terpisah dengan gerbang uji & aturan revert.
