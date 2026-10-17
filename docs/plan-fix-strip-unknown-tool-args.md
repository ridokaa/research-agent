# [STRIP-ARGS] Plan — Buang Argumen Tool Tak Dikenal (fix 400 dari param ekstra model)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: mencegah `HTTP 400` saat model menyisipkan
> **parameter yang tak ada di schema tool** (mis. minimax-m3 menambah `display_text`), dengan **menyaring
> argumen** ke properti yang dideklarasikan schema **sebelum** memanggil tool. Deterministik &
> **model-agnostik**. Audit branch `retrofit/chat-completions-adapter` (2026-08-07). Patuhi
> `docs/PROMPT_PLAN.md` §1.6. **Disiplin anti-regresi: fix + test + commit.**

---

## 1. Akar Masalah (TERBUKTI dari log run `792b0a2c`, 2026-08-07)

Setelah ganti model ke `xkiro/minimax/minimax-m3`, **semua** panggilan tool Verosight mengembalikan
`HTTP 400`. Penyebabnya: model menyuntikkan field ekstra **`display_text`** ke setiap argumen:
```
get_keyword_volume: {days:30, keyword:'IKN', display_text:'Cek volume IKN 30 hari terakhir'}
get_sentiment:      {days:30, limit:100, query:'IKN', display_text:'...'}
search_posts:       {keyword:'IKN', limit:15, sort:'like_count', display_text:'...'}
```
`display_text` **tidak ada di schema tool Verosight** → Verosight menolak param tak dikenal → **400**.
Keyword `"IKN"` sendiri **benar** (terbukti jalan di probe). Yang merusak hanya param ekstra ini.

**Kenapa validasi schema tak menangkap:** schema Verosight **tak** menyetel `additionalProperties: false`,
jadi param ekstra **lolos** `jsonschema.validate` lalu dikirim apa adanya ke Verosight (`executor.py:288`).

Ini **quirk model** (minimax-m3 suka menambah label narasi ke arg tool), tapi solusinya di **lapisan
executor** agar kebal untuk model apa pun.

---

## 2. Desain — helper `_strip_unknown_arguments`

**File:** `app/tools/executor.py`. Sisipkan helper baru + panggil **sebelum** `jsonschema.validate`
(`executor.py:288`), mengikuti pola `_normalize_date_arguments` (baris 281) & `_normalize_platform_arguments`
(baris 284):
```python
args_dict = self._normalize_date_arguments(tool, args_dict)
args_dict = self._normalize_platform_arguments(tool, args_dict, user_query=user_query)
args_dict = self._strip_unknown_arguments(tool, args_dict)   # <-- BARU, sebelum validate
```
Fungsi:
```python
def _strip_unknown_arguments(self, tool, args_dict):
    schema = tool.spec.parameters if (tool and tool.spec) else None
    if not isinstance(schema, dict):
        return args_dict
    props = schema.get("properties")
    # Hanya saring bila schema mendeklarasikan set properti eksplisit
    # DAN tidak sengaja mengizinkan properti tambahan.
    if not isinstance(props, dict) or schema.get("additionalProperties") is True:
        return args_dict
    allowed = set(props.keys())
    stripped = [k for k in args_dict if k not in allowed]
    if stripped:
        log.info("tool.stripped_unknown_args", tool=tool.spec.name, stripped=stripped)
    return {k: v for k, v in args_dict.items() if k in allowed}
```
**Prinsip:** hanya saring **top-level keys** yang tak ada di `properties`. Lewati bila
`additionalProperties: true` (schema sengaja bebas). Log param yang dibuang (observability).

---

## 3. Verifikasi & Gerbang Uji

- [ ] **Unit test:** tool dgn schema `{properties:{a}}`, dipanggil `{a:1, display_text:"x"}` →
      tool menerima **hanya** `{a:1}` (`display_text` terbuang); tool dgn `additionalProperties:true`
      → arg tak diubah. Suite penuh tetap **hijau**.
- [ ] **Gerbang uji (≥3 run) dgn minimax-m3:** query "IKN"/"kopdes" → arg tool **tanpa** `display_text`
      → tool **sukses (bukan 400)** → data kembali → jawaban kaya. Log memuat `tool.stripped_unknown_args`.
- [ ] `python -m py_compile app/tools/executor.py` OK; rahasia teredaksi; commit (tanpa push kecuali diminta).

---

## 4. Catatan Lanjutan (setelah fix ini)

- **Baru setelah tool sukses**, kita bisa menilai **kesetiaan sintesis minimax-m3 yang sebenarnya** di agen
  nyata (bug "menyangkal data" belum bisa diuji karena selama ini tool gagal 400 lebih dulu). Uji terkontrol
  menunjukkan minimax-m3 **setia** (pakai sentimen + pemicu) — konfirmasi di agen nyata setelah fix ini.
- **Observability (terpisah, opsional):** event `Executed tool → success` tetap muncul walau konten MCP
  `is_error=True` (400). `memory_write` sudah benar `status='error'`. Pertimbangkan set `status=error` pada
  event `tool_result` juga — lihat memori `verosight-keyword-and-tool-quirks`.

---

## 5. Risiko & Batasan (jujur)

- **RS1 — Tool yang sengaja bebas param.** Bila ada tool dgn `additionalProperties:true`, guard melewatinya
  (arg tak disaring). Aman.
- **RS2 — Schema tak lengkap.** Bila param sah tapi lupa didaftarkan di `properties`, ia ikut terbuang.
  Untuk tool Verosight schema-nya lengkap, jadi risiko rendah; log `stripped` membantu mendeteksi.
- **RS3 — Hanya top-level.** Nested keys tak disentuh — cukup untuk kasus ini (`display_text` top-level).
- **RS4 — Ini fix EXECUTOR (lapisan tool), bukan LLM.** Berlaku untuk model apa pun; melengkapi
  normalisasi tanggal/platform yang sudah ada. Quirk `display_text` sendiri milik minimax-m3.
- **Rujukan:** memori `verosight-keyword-and-tool-quirks`, `docs/plan-fix-tanggal-otomatis-sistem.md`
  (pola normalisasi arg), `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit `executor.py` + test, uji ≥3 run, commit) pada tahap
  terpisah.
