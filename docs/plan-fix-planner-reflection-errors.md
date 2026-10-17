# [PLANNER-REFLECTION-FIX] Plan — Perbaiki Error dari Editan `planner.py` (agar test `test_planner_reflection.py` HIJAU)

> **Dokumen rencana untuk agen eksekutor / untuk dikoreksi user sendiri.** Scan proyek menemukan
> **1 error fatal** (SyntaxError) plus **2 kegagalan test** yang terungkap oleh test buatan user sendiri
> (`tests/test_planner_reflection.py`). Semua berasal dari editan terakhir pada
> `app/agent/planner.py:103-122` (`build_reflection_input`). Perbaikan bersifat **1 karakter typo +
> teks prompt + urutan 2 blok** → **risiko rendah, 100% reversibel via git**. Audit branch
> `retrofit/chat-completions-adapter` (2026-08-10). Patuhi `docs/PROMPT_PLAN.md` §1.6.
> **Disiplin: fix + test + commit.**
>
> **CATATAN:** plan ini adalah **satu-satunya sumber** untuk penyelarasan `reflection.md`/`planner.py`
> (plan lama `plan-fix-reflection-template-prepend-alignment.md` sudah dihapus & diserap ke sini).
> Urutan reason↔missing **WAJIB** ditukar (bukan opsional), karena test user
> (`test_planner_reflection.py:16`) menuntut reason muncul sebelum missing.

---

## 1. Hasil Scan (TERBUKTI, 2026-08-10)

### Error #1 — FATAL: `SyntaxError` di `app/agent/planner.py:118`
```python
return {11          # <-- '11' nyasar dari editan
    "type": "message",
    ...
```
`python -m py_compile app/agent/planner.py` → `SyntaxError: invalid syntax. Perhaps you forgot a comma?`.
Karena ini memutus *import* `planner.py`, seluruh aplikasi **tak bisa start** dan **10 file test gagal
collection**:
```
ERROR tests/test_agent_loop.py, test_api_endpoints.py, test_hardening.py, test_health.py,
      test_mcp.py, test_planner_reflection.py, test_security.py, test_state.py,
      test_synthesis_right_sizing.py, test_verosight.py
Interrupted: 10 errors during collection
```
**Ini prioritas #1** — tanpa ini beres, tak ada test yang bisa jalan.

### Error #2 — Placeholder mentah bocor (setelah #1 diperbaiki)
`app/agent/prompts/reflection.md` baris 1–2 MASIH memuat `{reason}` / `{missing}`, tapi kode **tak lagi**
meng-`.format()` template (kini prepend). Placeholder tak pernah terisi → terkirim mentah.
`test_planner_reflection.py:19-20` (`assert "{reason}" not in content` / `"{missing}" not in content`)
→ **GAGAL**.

### Error #3 — Urutan reason/missing terbalik (setelah #1 diperbaiki)
Kode sekarang prepend `reason` dulu (baris 108-109) lalu `missing` (baris 111-112). Karena keduanya
prepend (`temp = BARU + temp` → yang di-prepend TERAKHIR ada paling DEPAN), `missing` berakhir
**di depan** → urutan output = `[missing][reason][template]`.
`test_planner_reflection.py:16` menuntut **reason SEBELUM missing**
(`index("Alasan: belum lengkap") < index("Informasi yang masih kurang: data sentimen")`) → **GAGAL**.

**Asal-usul (dari `git diff HEAD -- app/agent/planner.py`):** editan terakhir user **memindah blok `reason`
ke ATAS blok `missing`** (HEAD: missing dulu, reason kedua → user: reason dulu, missing kedua). Karena
prepend membalik urutan, memindah `reason` ke atas justru menurunkannya ke belakang di output. **Urutan blok
HEAD (missing dulu) SUDAH menghasilkan reason-di-depan** — sesuai test. Jadi Error #3 lahir dari reorder
tersebut; perbaikannya = kembalikan urutan blok ke HEAD (lihat Perubahan 2).

> **Sumber kebenaran kontrak = test user.** `tests/test_planner_reflection.py` sudah ada & mengkodekan
> maksud user: (a) tak ada placeholder mentah, (b) reason sebelum missing, (c) kasus `None` = daftar
> langkah saja. Perbaikan diarahkan agar ketiga test itu HIJAU.

**Pemanggil tunggal:** `app/agent/loop.py:472` → `build_reflection_input(missing=missing, reason=reason)`
(keyword args → penukaran urutan blok internal AMAN, tak memengaruhi call-site).

---

## 2. Desain Perbaikan (3 perubahan)

### Perubahan 1 — `app/agent/planner.py:118`: hapus `11` nyasar (FATAL)
```python
return {            # dari:  return {11
    "type": "message",
    "role": "user",
    "content": content,
}
```
> **CATATAN kepemilikan file:** `planner.py` adalah file yang USER koreksi sendiri. Bila plan ini dieksekusi
> agen, konfirmasi dulu ke user sebelum menyentuh `planner.py`; atau user menerapkan sendiri satu-baris ini.

### Perubahan 2 — `app/agent/planner.py:108-112`: TUKAR urutan blok agar reason di depan (Error #3)
**Ini membalik editan terakhir Anda** pada baris 108–112 (yang menaruh blok `reason` di atas). Karena
prepend membalik urutan, untuk mendapat output `[reason][missing][template]`, blok `missing` harus
di-prepend **lebih dulu**, blok `reason` **kedua** (= persis urutan blok di HEAD). Ganti baris 108–112:

**DARI (kondisi sekarang, baris 108–112):**
```python
if reason:
    temp= "Jawabanmu belum bisa dianggap final. Alasan: {x}. ".format(x=reason) + temp

if missing:
    temp = "Informasi yang masih kurang: {y}. ".format(y=missing) + temp
```
**MENJADI (missing dulu, reason kedua):**
```python
if missing:
    temp = "Informasi yang masih kurang: {y}. ".format(y=missing) + temp

if reason:
    temp = "Jawabanmu belum bisa dianggap final. Alasan: {x}. ".format(x=reason) + temp
```
Verifikasi cepat: `temp = template` → prepend missing → `"MISSING… template"` → prepend reason →
**`"REASON… MISSING… template"`** → reason di depan → memenuhi `test_planner_reflection.py:16`.
(Bereskan juga baris kosong ganda 113-115 & rapikan `temp=` → `temp =` di baris 109 bila mau — kosmetik.)

> **CATATAN untuk user:** karena Anda sengaja menaruh `reason` di atas, mungkin secara intuitif mengira itu
> membuat reason "tampil dulu". Semantik prepend membalikkannya. Bila Anda ingin **kode terbaca** reason-dulu
> DAN **output** reason-dulu sekaligus, alternatifnya pakai *append* untuk yang kedua (mis. rakit list lalu
> `"".join`) — tapi itu menambah kompleksitas; menukar urutan blok (di atas) adalah cara paling minim.

### Perubahan 3 — `app/agent/prompts/reflection.md`: HAPUS baris framing 1–2 (Error #2)
Buang dua baris placeholder (dan baris kosong pengikutnya) agar file DIMULAI dari daftar langkah. Framing
`reason`/`missing` kini sepenuhnya milik kode (prepend). Isi SESUDAH edit:
```
Tentukan satu langkah konkret berikutnya:
- Pahami sinyal atau anomali dari hasil tool sebelumnya: jika ada lonjakan volume tak wajar, sentimen ekstrim, atau dominasi platform yang relevan dengan pertanyaan, pertimbangkan untuk mencari pemicunya (mis. via `search_posts` atau `semantic_search`).
- Panggil tool lain yang belum pernah dicoba dan relevan.
- Panggil tool yang sama dengan argumen berbeda (mis. perjelas lokasi atau rentang waktu).
- Simpulkan dengan data yang ada bila informasi memang tidak tersedia atau tool pelengkap tidak ada.
Jangan mengulangi panggilan yang sudah terbukti kosong atau gagal.
```

**Kenapa ketiganya harus bersama:** Perubahan 1 membuat kode bisa di-*import*; tanpa Perubahan 3 test
`{reason}`/`{missing}` tetap gagal; tanpa Perubahan 2 test urutan tetap gagal. Ketiganya menghijaukan
`test_planner_reflection.py`.

---

## 3. Tahapan (SATU tahap sekali jalan)

1. Terapkan Perubahan 1 (typo — FATAL), Perubahan 2 (urutan), Perubahan 3 (template).
2. `./.venv/bin/python -m py_compile app/agent/planner.py` → harus OK.
3. Jalankan test sasaran: `./.venv/bin/python -m pytest tests/test_planner_reflection.py -q` → **2 passed**.
4. Jalankan suite penuh: `./.venv/bin/python -m pytest tests/ --ignore=tests/test_llm_live.py -q`
   → kembali **hijau** (10 error collection hilang).
5. **Commit** (tanpa push kecuali diminta). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
   Ingat: `.env` gitignored; kecualikan `AgenticTools/` dari commit.

**Gerbang uji (≥3 run agen nyata) — memicu jalur refleksi (self-check `is_final=false`):**
- Query yang cenderung memicu refleksi (mis. pertanyaan signifikansi menuntut dimensi tambahan).
- **Kriteria lulus:** log `event=reflection` muncul; pesan refleksi memuat kalimat reason **lalu** missing,
  **tanpa** literal `{reason}`/`{missing}`, **tanpa** duplikasi; iterasi berikut jalan normal (tak ada
  regresi loop/latensi).

**Revert-if-worse:**
`git checkout <baseline> -- app/agent/planner.py app/agent/prompts/reflection.md`
(test tak perlu di-revert; ia justru penjaga).

---

## 4. Verifikasi Akhir

- [ ] `py_compile app/agent/planner.py` OK (tak ada `SyntaxError`).
- [ ] `reflection.md` tak lagi memuat `{reason}`/`{missing}`; dimulai dari daftar langkah.
- [ ] `build_reflection_input("data sentimen", "belum lengkap")` → reason sebelum missing, tanpa placeholder mentah.
- [ ] `build_reflection_input(None, None)` → daftar langkah saja.
- [ ] `test_planner_reflection.py` → **2 passed**; suite penuh **hijau**.
- [ ] Gerbang uji ≥3 run: log `event=reflection` bersih; tak ada regresi.
- [ ] Rahasia teredaksi; commit (tanpa push kecuali diminta).

---

## 5. Risiko & Batasan (jujur)

- **RS1 — `planner.py` milik user.** Perubahan 1 & 2 menyentuh file yang user koreksi sendiri. Bila
  dieksekusi agen, **konfirmasi dulu**; idealnya user menerapkan sendiri (typo 1 karakter + tukar 2 blok).
  Perubahan 3 (`reflection.md`) tak menyentuh `planner.py` → bisa dikerjakan terpisah.
- **RS2 — Hilangnya framing saat `None`.** Bila self-check memanggil `reason=None, missing=None`, model
  hanya menerima daftar langkah. Daftar langkah sudah mengarahkan; bila kurang tegas, pertimbangkan default
  `reason` ringan di kode (di luar cakupan plan ini).
- **RS3 — Perilaku prompt, bukan logika.** Test mengunci render string, bukan kualitas jawaban model.
  Gerbang uji ≥3 run adalah kontrol kualitasnya.
- **RS4 — Reversibel penuh** via git checkout; tak ada migrasi data/skema.
- **RS5 — Sumber tunggal.** Plan lama `plan-fix-reflection-template-prepend-alignment.md` sudah DIHAPUS &
  diserap ke plan ini (di sana penukaran urutan berstatus opsional; di sini WAJIB demi test user). Tak ada
  lagi dokumen tumpang tindih.
- **Rujukan:** `app/agent/planner.py:103-122`, `app/agent/loop.py:472`, `app/agent/prompts/reflection.md`,
  `tests/test_planner_reflection.py`, memori `agent-loop-reliability-state`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit + test + uji ≥3 run + commit) pada tahap terpisah.
