# [RESTORE-SYNTH] Plan — Pulihkan `synthesis_mode` (regresi tertimpa retry-on-empty)

> **Dokumen rencana + DITERAPKAN LANGSUNG di sesi ini** (atas permintaan pengguna, sekali ini).
> Tujuan: memulihkan logika `synthesis_mode` (right-size sintesis, Tahap 1+2 dari
> `plan-fix-sintesis-tools-none-proaktif.md`) yang **terhapus** saat agen lain memasang retry-on-empty,
> **sambil mempertahankan** retry-on-empty. Keduanya harus **hidup berdampingan**. Audit branch
> `retrofit/chat-completions-adapter` (2026-08-05). Patuhi `docs/PROMPT_PLAN.md` §1.6.

---

## 1. Akar Masalah (TERBUKTI)

Run `5118bd33` (query "prabowo", 2026-08-05 16:47): iter-2 memakai `tools=32` + `input_tokens=52945`
→ **empty karena UKURAN** → retry-on-empty mengulang panggilan 52k yang sama 2x → tetap empty → jatuh ke
`Fallback answer generated` (self-check dilewati).

**Penyebab:** `grep synthesis_mode app/agent/loop.py` = **nol** → logika `synthesis_mode` (gating
`tools=None` + right-size konteks sintesis) **hilang** dari `loop.py`, meski `build_synthesis_input`
masih ada (yatim) di `planner.py:133`. Agen yang memasang retry-on-empty menimpa blok itu.

**Konsekuensi:** retry-on-empty menargetkan panggilan **besar** (52k) yang empty-nya **karena ukuran** —
retry tak bisa memperbaiki empty akibat ukuran (hanya flake acak). Pipeline balik ke masalah asli.

---

## 2. Perbaikan (kembalikan kode yang sudah terbukti + jaga retry-on-empty)

Pulihkan 5 elemen di `app/agent/loop.py`:

1. **Deklarasi flag** `synthesis_mode: bool = False` sebelum `while True` (dekat `final_answer`).
2. **Right-size saat sintesis:** setelah `build_request`, bila `synthesis_mode and state.execution_memory`
   → `llm_input = build_synthesis_input(state.user_query, get_context_block(max_tokens=4000, rich=True))`.
   Tambah `"synthesis_mode"` ke payload `context_built`.
3. **Gating tool & prev_id:** `effective_tools = None if synthesis_mode else (tools_def or None)`;
   `prev_id = None if synthesis_mode else (last_response_id if store else None)`. Panggilan `respond`
   utama **dan** retry-on-empty **sama-sama** memakai `effective_tools`/`prev_id` (agar retry sintesis
   juga kecil).
4. **Set `synthesis_mode = True`** setelah blok eksekusi tool, sebelum `continue`.
5. **Reset `synthesis_mode = False`** setelah refleksi diinjeksi.

**Retry-on-empty (`loop.py:226-239`) DIPERTAHANKAN** — hanya diubah agar memakai `effective_tools`/`prev_id`.
Setelah pulih: sintesis kecil (`tools=0`, ~3-4k) → teks → **self-check** (`"Final answer verified and
produced"`); retry-on-empty menangkap sisa flake acak pada panggilan yang kini kecil.

---

## 3. Verifikasi (WAJIB, di sesi ini)

- [ ] `grep synthesis_mode app/agent/loop.py` → muncul (deklarasi, gating, set True, reset False).
- [ ] Retry-on-empty memakai `effective_tools`, bukan `tools_def` mentah.
- [ ] `python -m py_compile app/agent/loop.py` → OK.
- [ ] Uji ≥3 run: iter-2 `synthesis_mode=True`, `tools=0`, `input_tokens` kecil (~3-4k), `has_text=True`;
      final via `"Final answer verified and produced"` (bukan fallback/synthesis-retry).
- [ ] Bila flake acak muncul → log `"retrying attempt N/2"` lalu pulih.

---

## 4. Catatan & Batasan (jujur)

- **CT1 — Regresi terpisah yang DITEMUKAN (belum diperbaiki di sini):** self-check juga disederhanakan
  (`loop.py:398-401` kehilangan `rich=True` + `executed_tools`/`available_tools` + guard anti-loop dari
  `plan-agen-lebih-cerdas.md`). Plan ini **sengaja tidak** menyentuhnya agar perubahan fokus & rendah
  risiko. Perlu keputusan terpisah untuk memulihkannya.
- **CT2 — Pelajaran:** dua fix di file yang sama saling menimpa karena belum di-commit. **Commit setiap
  fix yang terbukti** sebagai checkpoint sebelum fix berikutnya.
- **RS — Delikat: logika loop.** Uji ≥3 run + revert-if-worse.
- **Rujukan:** `docs/plan-fix-sintesis-tools-none-proaktif.md` (sumber Tahap 1+2),
  `docs/plan-fix-retry-on-empty-self-check.md` (retry-on-empty), `docs/plan-agen-lebih-cerdas.md`
  (self-check), `docs/PROMPT_PLAN.md` §1.6.
