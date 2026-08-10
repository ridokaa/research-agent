# [DOUBLED-PREFIX-FIX] Plan — Jinakkan Nama Tool Prefix-Dobel (`verosight__verosight__…`) via repair-on-miss

> **Dokumen rencana untuk agen eksekutor / untuk diterapkan user sendiri.** Run nyata
> (`minimax-m2.7-highspeed`, 2026-08-10 16:35, run ke-4) menunjukkan model memanggil
> `verosight__verosight__search_posts` (prefix `verosight__` **dobel**) → `Tool ... not found or disabled`
> → dimensi ke-3 (pemicu viral) hilang. Ini **glitch model** (intermittent; 2 tool lain bernama benar),
> bukan bug kode. Perbaikan: **guard deterministik "repair-on-miss" di `executor.execute()`** — bila lookup
> gagal, runtuhkan prefix namespace yang terduplikasi lalu **coba lookup ulang; hanya dipakai bila cocok
> PERSIS** dengan tool terdaftar. **Tak bisa salah-arah**, model-agnostic, gaya sama seperti `strip-args`.
> Risiko rendah, 100% reversibel via git. Audit branch `retrofit/chat-completions-adapter` (2026-08-10).
> Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Bukti (TERBUKTI, run 2026-08-10 16:35)

```
Selected 3 tool(s): verosight__get_keyword_volume, verosight__get_sentiment, verosight__verosight__search_posts
Executed tool 'verosight__verosight__search_posts' -> error
  error="Tool 'verosight__verosight__search_posts' not found or disabled." duration_ms=0 status=error
```
- Nama terdaftar di registry = `verosight__search_posts` (`app/mcp/adapter.py:34`:
  `tool_name = f"{server_alias}__{tool.name}"` — prefix ditempel **sekali**).
- Dua tool lain (`get_keyword_volume`, `get_sentiment`) bernama **benar** (prefix tunggal). **Hanya**
  `search_posts` yang dobel → berarti model (minimax-m2.7) **menduplikasi** token `verosight__` pada satu
  panggilan. Intermittent → run 1–3 bersih.
- `executor.execute()` (`app/tools/executor.py:311`): `tool = self._registry.get(name)` → `None` →
  blok "not found or disabled" (l.313-329). Tak ada toleransi terhadap nama ternodai.

**Dampak:** `search_posts` gagal → hanya 2/3 dimensi terkumpul. Run **tetap** `status=success` (jawaban
volume+sentimen kaya) — jadi ini **degradasi kualitas** (kehilangan bukti pemicu/konten viral), bukan crash.
Tak berhubungan dengan fix multiword (exclude_keywords run itu sudah bersih: `["musik","konser"]`).

---

## 2. Prinsip desain — kenapa "repair-on-miss" AMAN

Kunci keamanan: **jangan pernah mengubah nama yang sudah valid, dan jangan pernah mengarah ke tool lain.**

- Guard **hanya aktif setelah `registry.get(name)` mengembalikan `None`** (lookup normal gagal). Nama yang
  sudah benar tak pernah tersentuh → **nol regresi** untuk jalur normal.
- Setelah miss: hitung `repaired = dedupe_prefix(name)` (runtuhkan pengulangan token `alias__alias__…` →
  `alias__`). Lalu **lookup ulang** `registry.get(repaired)`.
- **Pakai `repaired` HANYA bila ia cocok PERSIS** dengan tool terdaftar. Jika tidak cocok → tetap
  "not found or disabled" seperti semula. → **Mustahil mengarah ke tool yang salah**, karena syaratnya
  adalah hit eksak di registry. Bahkan bila regex dedup tak sempurna, kegagalan cocok cuma jatuh ke error
  lama (tak ada efek samping).
- Log `tool.name_deduplicated` agar glitch model tetap terlihat (tak menyembunyikan sinyal).

---

## 3. Perubahan — `app/tools/executor.py`

### 3.A — Helper `_dedupe_namespace_prefix` (BARU)
```python
import re

_DUP_PREFIX_RE = re.compile(r"^([A-Za-z0-9]+__)\1+")

def _dedupe_namespace_prefix(self, name: str) -> str:
    """Collapse a repeated leading namespace token, e.g. 'verosight__verosight__x' -> 'verosight__x'.

    Only the leading duplicated '<alias>__' run is collapsed; the tool base name (after the first
    '__') is untouched. Returns name unchanged if no duplicated prefix is present.
    """
    if not name or "__" not in name:
        return name
    return self._DUP_PREFIX_RE.sub(r"\1", name)
```
Catatan: `[A-Za-z0-9]+__` mencocokkan token alias (mis. `verosight__`); `\1+` menyerap satu-atau-lebih
pengulangannya. `search_posts` (setelah `__` pertama) tak tersentuh. Alias Verosight tanpa underscore →
aman.

### 3.B — Repair-on-miss di `execute()` (sekitar `app/tools/executor.py:311`)
**DARI:**
```python
tool = self._registry.get(name)

if tool is None:
    res = ToolResult(
        tool_name=name,
        status="error",
        error=f"Tool '{name}' not found or disabled.",
        duration_ms=0,
    )
    ...
    return res
```
**MENJADI:**
```python
tool = self._registry.get(name)

if tool is None:
    repaired = self._dedupe_namespace_prefix(name)
    if repaired != name:
        candidate = self._registry.get(repaired)
        if candidate is not None:
            log.info("tool.name_deduplicated", original=name, resolved=repaired)
            name = repaired            # pakai nama yang benar untuk sisa eksekusi
            tool = candidate

if tool is None:
    res = ToolResult(
        tool_name=name,
        status="error",
        error=f"Tool '{name}' not found or disabled.",
        duration_ms=0,
    )
    ...
    return res
```
Setelah `name`/`tool` dipulihkan, sisa `execute()` (parse args, normalizer, validate, panggil) berjalan
normal terhadap tool yang benar. **Panggilan pertama langsung berhasil** — tak perlu retry.

> **Alternatif (tidak dipilih):** normalisasi di adapter LLM saat parse `tool_calls` (di sumber). Ditolak
> karena `execute()` adalah **chokepoint tunggal** semua pemanggilan tool → satu titik, cakupan penuh,
> paling minim.

---

## 4. Konsistensi dengan pekerjaan lain

1. **Tidak menyentuh `planner.py`** (file yang Anda koreksi). Hanya `executor.py`.
2. **Sejalan pola yang ada** — `strip-args`, `_normalize_date/platform`, `_sanitize_keyword_list`: guard
   deterministik ber-log di `executor`. Ini menambah satu lagi jaring pengaman model-agnostic.
3. **Ortogonal** dengan fix reload (`Makefile`), no-geo caveat & multiword (sudah commit). Tak beririsan.
4. **Tidak menyembunyikan bug model** — event `tool.name_deduplicated` mencatat tiap koreksi.

---

## 5. Tahapan (SATU tahap)

1. Terapkan §3.A (helper + regex) & §3.B (repair-on-miss).
2. **Unit test** di `tests/`:
   - `_dedupe_namespace_prefix("verosight__verosight__search_posts") == "verosight__search_posts"`.
   - `_dedupe_namespace_prefix("verosight__search_posts") == "verosight__search_posts"` (tak berubah).
   - `_dedupe_namespace_prefix("verosight__get_keyword_volume")` tak berubah (base name ber-underscore aman).
   - `execute("verosight__verosight__<tool valid>", …)` → **sukses** memanggil tool yang benar +
     event `tool.name_deduplicated` (pakai registry/tool tiruan).
   - `execute("verosight__benar-benar-tak-ada", …)` → tetap `status='error'` "not found or disabled"
     (repair tak menciptakan match palsu).
3. `./.venv/bin/python -m pytest tests/ --ignore=tests/test_llm_live.py -q` → **hijau**.
4. **Commit** (tanpa push kecuali diminta). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
   `.env` gitignored; kecualikan `AgenticTools/`.

**Gerbang uji (≥3 run agen nyata):**
- Jalankan lewat **`make run`** (tanpa reload) agar uji tak terganggu. Ulang query signifikansi yang memicu
  3 tool (mis. "apakah IKN/festival/nuklir sering dibicarakan?") beberapa kali sampai glitch prefix-dobel
  muncul lagi (intermittent).
- **Kriteria lulus:** saat model mengeluarkan `verosight__verosight__…`, log `tool.name_deduplicated`
  muncul dan tool **tetap tereksekusi sukses** (dimensi ke-3 terpakai); tak ada "not found or disabled"
  untuk kasus prefix-dobel. Nama yang benar tetap jalan seperti biasa.
- **Revert-if-worse:** `git checkout <baseline> -- app/tools/executor.py`.

---

## 6. Verifikasi Akhir

- [ ] `_dedupe_namespace_prefix` ada; hanya meruntuhkan prefix alias terduplikasi (base name aman).
- [ ] Repair **hanya** aktif setelah miss; **hanya** dipakai bila hit registry eksak.
- [ ] Event `tool.name_deduplicated` tercatat saat koreksi terjadi.
- [ ] Nama valid tak berubah; nama benar-benar tak-ada tetap error (unit test).
- [ ] Suite penuh hijau.
- [ ] Gerbang uji ≥3 run: prefix-dobel tersembuhkan; dimensi ke-3 kembali.
- [ ] Rahasia teredaksi; commit (tanpa push kecuali diminta).

---

## 7. Risiko & Batasan (jujur)

- **RS1 — Hanya menutup pola prefix-DOBEL.** Bukan segala halusinasi nama. Mis. typo base name
  (`verosight__serch_posts`) atau tool salah tetap "not found". Menangani itu (fuzzy match) **berisiko
  salah-arah** → sengaja **di luar cakupan**. Guard ini aman justru karena hanya meruntuhkan duplikasi +
  wajib hit eksak.
- **RS2 — Menambal gejala model, bukan akar.** Akar = minimax-m2.7 kadang menggandakan prefix. Guard
  membuatnya tak fatal, tapi bila ingin akar bersih, model yang lebih taat instruksi = solusi jangka panjang
  ([[agent-loop-reliability-state]]). Log `tool.name_deduplicated` memantau frekuensinya.
- **RS3 — Perilaku deterministik, terkunci unit test.** Tak seperti nudge prompt; ini logika murni →
  regression test cukup sebagai penjaga (gerbang uji ≥3 run untuk validasi end-to-end intermittent).
- **RS4 — Tak ada retry-on-error terpisah.** Guard menyembuhkan di **panggilan pertama** (nama dipulihkan
  sebelum eksekusi), jadi tak butuh retry. Kegagalan tool dari sebab LAIN tetap tak di-retry (isu terpisah).
- **RS5 — Reversibel penuh** via git checkout; tak ada migrasi/skema.
- **Rujukan:** `app/tools/executor.py:311-329`, `app/mcp/adapter.py:34`, `app/tools/registry.py:118`,
  run 2026-08-10 16:35, memori [[agent-loop-reliability-state]], `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (edit + test + uji ≥3 run + commit) pada tahap terpisah.
