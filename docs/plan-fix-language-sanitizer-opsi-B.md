# [LANG-SANITIZE] Plan — Opsi B: Pembersih Bahasa Output (risiko kesetiaan NOL)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: menghapus kebocoran token bahasa asing (Mandarin
> `帖子`, Rusia `упоминают`) dari jawaban akhir minimax-m3, **tanpa menyentuh perilaku sintesis model** →
> **risiko kesetiaan NOL** (pembersihan berjalan SETELAH model menjawab). Dilengkapi **saklar konfigurasi**
> + **jalur revert** bila tak puas. Audit branch `retrofit/chat-completions-adapter` (2026-08-07). Patuhi
> `docs/PROMPT_PLAN.md` §1.6. **Disiplin: fix + test + commit.**

---

## 1. Masalah (dari run minimax-m3, 2026-08-06/07)

minimax-m3 (model asal Tiongkok) **sesekali membocorkan token bahasa asing** ke jawaban Indonesia:
`帖子` (Mandarin = "postingan"), `упоминают`/`упоминаний` (Rusia = "menyebut/sebutan"). Ini **quirk model**,
muncul di beberapa run (prabowo, dll.).

**Kenapa Opsi B (bukan A/prompt):** menambah instruksi prompt (Opsi A) mengubah perilaku model → **risiko
kecil menurunkan kesetiaan** (yang sudah rapuh). Opsi B membersihkan **string output** setelah model
selesai → **tak mengubah sintesis sama sekali** → kesetiaan tak mungkin terpengaruh.

---

## 2. Desain — `_sanitize_language(text)` + saklar konfigurasi

**File:** `app/config.py` (+ helper di `app/agent/loop.py` atau util baru).

**2.1. Saklar konfigurasi (untuk revert mudah):**
```python
# app/config.py
AGENT_LANGUAGE_SANITIZE: bool = True   # set False untuk menonaktifkan tanpa ubah kode
```

**2.2. Helper pembersih:**
```python
_LANG_MAP = {          # token asing yang DIKETAHUI -> padanan Indonesia
    "帖子": "postingan",
    "упоминают": "menyebut",
    "упоминаний": "sebutan",
}
_NONLATIN_RE = re.compile(r"[一-鿿぀-ヿ가-힯Ѐ-ӿ]")  # CJK + Kana + Hangul + Cyrillic

def _sanitize_language(text: str) -> str:
    if not text:
        return text
    for foreign, indo in _LANG_MAP.items():
        text = text.replace(foreign, indo)
    sisa = _NONLATIN_RE.findall(text)
    if sisa:
        log.warning("answer.nonlatin_leftover", chars=sorted(set(sisa))[:20])  # untuk memperluas peta
    return text
```

**2.3. Titik panggil (SATU tempat, mencakup semua jalur):** terapkan pada `final_answer` **tepat sebelum
membangun `RunResult`** (setelah loop, sebelum `return RunResult(...answer=final_answer...)`), digerbang
saklar:
```python
if settings.AGENT_LANGUAGE_SANITIZE and final_answer:
    final_answer = _sanitize_language(final_answer)
```
Karena semua jalur (self-check terverifikasi, synthesis retry, fallback) berujung ke `final_answer` sebelum
`RunResult`, satu titik ini menutup semuanya.

**Prinsip:** hanya **ganti token yang diketahui** + **log** sisa non-Latin (untuk memperluas peta). JANGAN
strip karakter non-Latin mentah (bisa merusak kata/nama sah, mis. nama akun asing).

---

## 3. Verifikasi & Gerbang Uji

- [ ] **Unit test:** `_sanitize_language("setiap帖子 viral")` → `"setiap postingan viral"`; string Latin-only
      → tak berubah; string dgn CJK tak dikenal → di-log (bukan error). Suite penuh tetap **hijau**.
- [ ] **Gerbang uji (≥3 run):** jawaban akhir **tak lagi memuat `帖子`/Cyrillic**; **kesetiaan TIDAK berubah**
      (sentimen/pemicu tetap dipakai persis seperti sebelum sanitizer — karena model tak disentuh).
- [ ] Log `answer.nonlatin_leftover` membantu menemukan token baru untuk ditambah ke `_LANG_MAP`.
- [ ] `python -m py_compile` OK; rahasia teredaksi; commit.

---

## 4. Jalur REVERT (bila tak puas) — dua tingkat

1. **Nonaktif cepat (tanpa ubah kode):** set `AGENT_LANGUAGE_SANITIZE=false` di `.env` → restart. Sanitizer
   mati total; jawaban kembali apa adanya. **Instan, reversibel penuh.**
2. **Hapus total:** `git revert <commit>` (atau `git checkout <baseline> -- app/config.py app/agent/loop.py
   tests/...`). Menghapus helper + saklar + test.

Karena Opsi B **tak menyentuh model**, tak ada perilaku sintesis yang perlu dipulihkan — revert bersih.

---

## 5. Risiko & Batasan (jujur)

- **RS1 — Cakupan whack-a-mole.** Hanya token **diketahui** yang diganti; token baru perlu ditambah ke
  `_LANG_MAP` (log `nonlatin_leftover` memandu). Bukan penerjemah umum.
- **RS2 — Risiko kesetiaan = NOL.** Sanitizer berjalan pada string output, **tak mengubah sintesis** →
  klaim data tetap persis. (Inilah keunggulan Opsi B atas A.)
- **RS3 — Jangan strip mentah.** Mengganti hanya token yang dipetakan; strip karakter non-Latin sembarangan
  bisa merusak nama/akun sah → hanya di-log, tak dihapus paksa.
- **RS4 — Akar tetap di model.** Kebocoran bahasa milik minimax-m3; sanitizer menutupi gejalanya. Bila
  bahasa 100% Indonesia sangat kritis, model non-Mandarin (gpt-5.6-terra/Claude) tak punya masalah ini —
  tapi itu trade-off dengan kesetiaan.
- **Rujukan:** memori `agent-loop-reliability-state`, `docs/catatan/kesetiaan-sintesis.md`,
  `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit config/loop + test, uji ≥3 run, commit) pada tahap
  terpisah.
