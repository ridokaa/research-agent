# [EXCLUDE-KW-400-FIX] Plan — Cegah HTTP 400 dari `exclude_keywords` multi-kata (sanitasi sempit + penguat prompt)

> **Dokumen rencana untuk agen eksekutor / untuk diterapkan user sendiri.** Run nyata
> (`minimax-m2.7-highspeed`, 2026-08-10 15:59) menunjukkan `verosight__search_posts` gagal **HTTP 400**
> karena model menaruh elemen **multi-kata** `'saham IKN'` di `exclude_keywords`. Verosight menolak keyword
> ber-spasi (400). Akibatnya dimensi ke-3 (pemicu/konten viral) hilang; jawaban tetap sukses tapi kehilangan
> bukti substansi. Perbaikan: **sanitasi deterministik SEMPIT di `executor`** (buang elemen multi-kata dari
> list-exclude, **JANGAN** utak-atik `keyword`/`query` skalar) + **penguat prompt** `system.md`. Sejalan pola
> `_normalize_date/platform` & `strip-args` yang sudah ada. **Risiko rendah & 100% reversibel via git.**
> Audit branch `retrofit/chat-completions-adapter` (2026-08-10). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Bukti (TERBUKTI, run 2026-08-10 15:59–16:00)

Query: *"Apakah topik IKN adalah hal yang sering dibicarakan akhir ini?"* Iterasi 1 memilih 3 tool. Dua
sukses, satu gagal:
```
Executed tool 'verosight__search_posts' ... status='error'
error='Error: API error (HTTP 400): 400 Bad Request'  duration_ms=16779
arguments: {'exclude_keywords': ['saham', 'saham IKN', 'NSIT', 'ICN'],
            'keyword': 'IKN', 'limit': 15, 'sort': 'like_count'}
Recorded memory item for 'verosight__search_posts' ... status='error' data=None
```

**Akar (terkonfirmasi):** elemen **`'saham IKN'`** ber-spasi (multi-kata). Verosight menolak keyword
multi-kata → HTTP 400 ([[verosight-keyword-and-tool-quirks]]). Elemen lain (`'saham'`, `'NSIT'`, `'ICN'`)
satu-kata → sah. Jadi **satu elemen busuk menggagalkan seluruh call**.

**Kenapa lolos:**
- `app/agent/prompts/system.md:26` melarang multi-kata **hanya** untuk `keyword`/`query` — **tak menyebut
  `exclude_keywords`**. Model tak tahu elemen exclude pun harus satu-kata.
- `app/tools/executor.py` menormalkan `date` (`_normalize_date_arguments`, l.300) & `platform`
  (`_normalize_platform_arguments`, l.303), lalu `strip-args` (l.306) → `jsonschema.validate` (l.310).
  **Tak ada** sanitasi keyword-list. `'saham IKN'` lolos apa adanya → 400 di Verosight.

**Niat model sebenarnya benar:** ia mencoba menyingkirkan makna "IKN=saham" (vs Ibu Kota Nusantara) agar
hasil relevan — instingnya bagus, formatnya saja salah (mestinya cukup `'saham'`).

**Dampak:** hanya 2/3 dimensi terkumpul (volume + sentimen). Jawaban akhir tetap sukses, kaya, jujur, tanpa
caveat lokasi (fix no-geo tegak) — tapi **tanpa bukti pemicu/konten viral**. Ini kegagalan **pengumpulan
data**, bukan kesetiaan sintesis. Catatan: loop **tak me-retry** tool yang 400 dalam run yang sama, jadi
dimensi itu benar-benar hilang untuk run tersebut.

---

## 2. Prinsip desain — kenapa SEMPIT (menghindari regresi "data salah senyap")

Bahaya terbesar sanitasi keyword = **mengubah 400 yang terlihat menjadi keberhasilan yang diam-diam salah**.
Aturan per-field WAJIB dibedakan:

| Field | Bentuk | Perlakuan | Alasan / bahaya yang dihindari |
|---|---|---|---|
| `exclude_keywords`, `exclude_profiles` | **list** (penyaring noise) | **BUANG** elemen multi-kata; pertahankan yang single-kata; **JANGAN pecah** | kehilangan 1 filter ≪ topik salah. **Pecah** `'saham IKN'`→`['saham','IKN']` malah meng-exclude keyword utama `IKN` → hasil kosong (BENCANA). |
| `keyword`, `query` | **skalar** (subjek pencarian) | **JANGAN transform senyap.** Maksimal *collapse whitespace ganda*; tidak mengubah makna | `"Ibu Kota Nusantara"`→ambil "Ibu" = **data topik salah tanpa ketahuan**. Ini regresi terburuk → dilarang. Untuk skalar, andalkan **prompt** (§3.B) + biarkan 400 bila model membandel. |
| lainnya | — | tidak disentuh | cegah over-reach ke arg sah ber-spasi |

**Konsekuensi jujur:** karena `keyword`/`query` skalar TIDAK diperbaiki di kode, bila model mengirim keyword
multi-kata ia tetap 400 & dimensinya hilang dalam run itu (seperti sekarang). Itu **trade-off yang benar**:
lebih baik gagal terbuka daripada menarik data topik yang salah secara senyap. Penguatan prompt (§3.B)
mengurangi frekuensinya.

---

## 3. Perubahan

### 3.A — Kode: `app/tools/executor.py` — `_sanitize_keyword_list_arguments` (BARU)

Metode baru, dipanggil **setelah** `_normalize_platform_arguments` dan **sebelum** `jsonschema.validate`
(sisipkan di antara baris 303 dan 306, mengikuti pola normalizer lain):

```python
# Sanitize list-type keyword filters (drop multi-word elements; scalar keyword/query untouched)
args_dict = self._sanitize_keyword_list_arguments(tool, args_dict)
```

Perilaku metode (skema-driven, defensif):
- **Hanya** untuk tool `tool.spec.source == "verosight"` (batasi cakupan).
- **Hanya** memproses key list yang namanya persis di whitelist: `{"exclude_keywords", "exclude_profiles"}`
  **dan** ada di `tool.spec.parameters["properties"]` (schema-driven, tak menebak).
- Untuk tiap key tsb yang bernilai `list`: pertahankan elemen `str` yang **satu-kata** (tak mengandung
  whitespace setelah `.strip()`); **buang** elemen multi-kata / kosong. Tak pernah **memecah** elemen.
- **Tidak menyentuh** `keyword`/`query` skalar (lihat §2). *(Opsional aman: `keyword`/`query` boleh
  di-`.strip()` untuk membuang spasi tepi — TIDAK mengubah makna; tandai opsional.)*
- **Log observability** bila ada yang dibuang: `log.info("tool.sanitized_keyword_list", tool=..., field=...,
  dropped=[...])` (meniru `tool.stripped_unknown_args`).
- Jika hasil list menjadi **kosong**, hapus key-nya dari args (jangan kirim list kosong yang bisa memicu
  perilaku tak terduga) — kembalikan args tanpa key itu.
- Idempoten & non-mutating (buat `dict(args_dict)` baru), seperti normalizer lain.

**Verifikasi cepat (kasus run):** `exclude_keywords=['saham','saham IKN','NSIT','ICN']`
→ buang `'saham IKN'` → `['saham','NSIT','ICN']` → call sah → `search_posts` sukses → dimensi ke-3 kembali.

### 3.B — Prompt: `app/agent/prompts/system.md:26` — perluas aturan satu-kata ke exclude

Tambahkan kalimat pada butir baris 26 (yang sudah melarang multi-kata untuk `keyword`/`query`):

> …**Aturan satu-kata ini juga berlaku untuk SETIAP elemen `exclude_keywords`/`exclude_profiles`** — mis.
> gunakan `['saham']`, **bukan** `['saham IKN']`. Elemen ber-spasi akan ditolak Verosight.

Ini mengurangi frekuensi model membuat kesalahan (baik untuk skalar maupun list), sementara §3.A jadi jaring
pengaman deterministik khusus list.

---

## 4. Konsistensi dengan pekerjaan lain

1. **Tidak menyentuh `planner.py`** (file yang Anda koreksi). Menyentuh `executor.py` + `system.md` saja.
2. **Sejalan pola yang ada:** `_normalize_date_arguments`, `_normalize_platform_arguments`,
   `_strip_unknown_arguments` — normalizer deterministik, schema-driven, ber-log. `_sanitize_keyword_list`
   mengikuti gaya & titik pemanggilan yang sama (sebelum `jsonschema.validate`).
3. **Tidak mengganggu fix lain:** no-geo caveat (`system.md:22`), reload fix (`Makefile`), strip-args tetap
   utuh. Penguat §3.B menambah kalimat di butir yang berbeda (l.26), tak menyentuh l.22.
4. **Tidak menyembunyikan bug model** — event `tool.sanitized_keyword_list` tetap mencatat koreksi.

---

## 5. Tahapan (SATU tahap)

1. Terapkan §3.A (metode + pemanggilan) & §3.B (kalimat prompt).
2. Tambah **unit test** di `tests/` (kunci perilaku deterministik):
   - `exclude_keywords=['saham','saham IKN','NSIT','ICN']` → `['saham','NSIT','ICN']`.
   - Semua elemen multi-kata → key dihapus dari args.
   - `keyword='Ibu Kota Nusantara'` (skalar) → **tetap** `'Ibu Kota Nusantara'` (TIDAK diubah) — mengunci
     jaminan "skalar tak disentuh" (mencegah regresi data-salah-senyap).
   - Tool non-Verosight / tanpa properti exclude → args tak berubah (no over-reach).
3. `./.venv/bin/python -m pytest tests/ --ignore=tests/test_llm_live.py -q` → **hijau**.
4. **Commit** (tanpa push kecuali diminta). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
   `.env` gitignored; kecualikan `AgenticTools/`.

**Gerbang uji (≥3 run agen nyata):**
- Ulang query pemicu ("apakah IKN sering dibicarakan?") + ≥2 query yang cenderung memancing exclude
  (topik ambigu: "gojek" vs "go-jek", "bbm" bahan bakar vs chat, dll.).
- **Kriteria lulus:**
  - `search_posts` **tidak** lagi 400 karena elemen exclude multi-kata; dimensi ke-3 (pemicu) ikut terpakai.
  - Log `tool.sanitized_keyword_list` muncul saat & hanya saat ada elemen multi-kata.
  - **Tidak ada** kasus baru: keyword utama tak sengaja ter-exclude / topik hasil berubah (cek `keyword`
    di `tool_result` tetap sesuai maksud).
  - Kesetiaan sintesis tak turun; tanpa caveat lokasi palsu (fix no-geo tetap tegak).
- **Revert-if-worse:** `git checkout <baseline> -- app/tools/executor.py app/agent/prompts/system.md`
  (test boleh tetap sebagai penjaga).

---

## 6. Verifikasi Akhir

- [ ] `_sanitize_keyword_list_arguments` ada, dipanggil sebelum `jsonschema.validate`, hanya untuk Verosight
      & whitelist key list.
- [ ] `keyword`/`query` skalar **terbukti tak diubah** (unit test).
- [ ] `system.md:26` memuat aturan satu-kata untuk elemen `exclude_keywords`/`exclude_profiles`.
- [ ] Unit test baru hijau; suite penuh hijau.
- [ ] Gerbang uji ≥3 run: 400 exclude hilang; dimensi pemicu kembali; tak ada topik-salah; log sanitasi muncul.
- [ ] Rahasia teredaksi; commit (tanpa push kecuali diminta).

---

## 7. Risiko & Batasan (jujur)

- **RS1 — Kehilangan filter exclude.** Membuang elemen multi-kata berarti hasil sedikit lebih noisy (filter
  itu tak berlaku). Ini **disengaja** & jauh lebih ringan daripada 400 atau topik salah. Diterima.
- **RS2 — `keyword`/`query` skalar tetap bisa 400.** By design (§2) — untuk mencegah data-salah-senyap, skalar
  tak diperbaiki di kode; hanya prompt (§3.B). Bila model membandel multi-kata di `keyword`, dimensi itu tetap
  hilang dalam run tsb. Penanganan skalar yang lebih agresif **sengaja di luar cakupan** (berisiko regresi).
- **RS3 — Perilaku prompt (§3.B) tak terkunci test.** Hanya §3.A yang deterministik & ber-unit-test; §3.B
  dikontrol gerbang uji ≥3 run. Test string-presence opsional untuk menjaga kalimat tak terhapus.
- **RS4 — Whitelist key statis.** Bila Verosight menambah param keyword-list lain, ia tak otomatis tercakup.
  Mitigasi: dokumentasikan whitelist; tambah bila skema berubah. Alternatif "deteksi via tipe schema
  (array of string bernama *keyword*)" lebih otomatis TAPI berisiko over-reach → sengaja dihindari.
- **RS5 — Loop tak retry tool 400.** Plan ini mencegah 400 exclude, bukan menambah retry-on-400. Bila 400
  muncul dari sebab lain, dimensi tetap hilang dalam run — isu terpisah (di luar cakupan).
- **RS6 — Reversibel penuh** via git checkout; tak ada migrasi/skema.
- **Rujukan:** `app/tools/executor.py:300-310`, `app/agent/prompts/system.md:26`, run 2026-08-10 15:59,
  memori [[verosight-keyword-and-tool-quirks]], [[agent-loop-reliability-state]], `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit + test + uji ≥3 run + commit) pada tahap terpisah.
