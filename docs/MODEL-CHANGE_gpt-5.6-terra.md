# [MODEL-CHANGE] Plan — Ganti Model LLM ke `cx/gpt-5.6-terra`

> **Dokumen rencana untuk agen eksekutor (bukan implementasi kode besar).** Tujuan: mengganti model LLM
> dari `xkiro/deepseek/deepseek-v4-pro` ke **`cx/gpt-5.6-terra`**, **key & endpoint 9Router SAMA**, gaya
> `chat_completions` SAMA. Disesuaikan dengan seluruh progres (synthesis_mode, retry-on-empty, right-size).
> Audit branch `retrofit/chat-completions-adapter` (2026-08-06). Patuhi `docs/PROMPT_PLAN.md` §1.6.
>
> **Penanda khusus:** file `MODEL-CHANGE_*` = perubahan model. (Beda dari `Plan-perubahan-model.md` lama
> yang soal deepseek.)

---

## 1. Ringkasan

Perubahan **minimal**: hanya satu baris di `.env` (`LLM_MODEL`). Model `cx/gpt-5.6-terra` **sudah diuji
langsung** ke 9Router dengan key saat ini dan **terbukti bekerja** (lihat §2). Semua infrastruktur lain
(gaya chat_completions, base_url, key, `store=false`, temperature, tools) **tidak berubah**.

Seluruh progres loop (synthesis_mode, retry-on-empty, right-size sintesis) bersifat **model-agnostik** →
otomatis ikut berlaku. Tak ada kode yang perlu dihapus.

---

## 2. Bukti Uji Pra-Terbang (SUDAH dijalankan 2026-08-06)

`POST https://9router.fhs.my.id/v1/chat/completions` dengan key `.env` saat ini:

| Uji | Payload | Hasil |
|---|---|---|
| Ping dasar | `model:"cx/gpt-5.6-terra", messages:[ping], max_tokens:5` | **HTTP 200**, `content:"pong"`, `object:"chat.completion"`, 1.74s |
| Temperature | + `temperature:0.2` | **HTTP 200** — model **menerima** temperature |
| Function calling | + `tools:[get_weather], tool_choice:auto` | **HTTP 200** — balas `tool_calls` format OpenAI standar, `finish_reason:"tool_calls"` |

**Kesimpulan:** model tersedia, mendukung temperature & function-calling dalam format `chat_completions`
yang sama → **drop-in di level config.** Respons `id` berawalan `resp_...` (gaya GPT-5), tapi `object` &
`choices` tetap standar → adapter kita (`app/llm/client.py`, parsing `choices`) kompatibel.

**Catatan biaya (PENTING):** `prompt_tokens` baseline ~**2.486** untuk input sepele "ping" (kemungkinan
overhead reasoning/system model). Dengan `TOOL_RESULT_PASSTHROUGH=true`, request nyata bisa besar →
**pantau biaya/latensi**. Right-size sintesis (sudah ada) membantu menekan ini.

### Cara agen mengulang uji (opsional, disarankan sebelum & sesudah)
```bash
KEY=$(grep -E '^LLM_API_KEY=' .env | cut -d= -f2-)
BASE=$(grep -E '^LLM_BASE_URL=' .env | cut -d= -f2-)
curl -s -X POST "$BASE/chat/completions" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"cx/gpt-5.6-terra","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
# Harap: HTTP 200 + content non-kosong.
```

---

## 3. Perubahan (INTI — satu baris)

**File:** `.env` (gitignored — TIDAK di-commit; ubah manual di mesin).
```
# SEBELUM
LLM_MODEL=xkiro/deepseek/deepseek-v4-pro
# SESUDAH
LLM_MODEL=cx/gpt-5.6-terra
```
**Tidak berubah:** `LLM_API_STYLE=chat_completions`, `LLM_BASE_URL=https://9router.fhs.my.id/v1`,
`LLM_API_KEY` (sama), `LLM_STORE=false`, `LLM_TEMPERATURE=0.2`, `LLM_MAX_OUTPUT_TOKENS=4096`,
`TOOL_RESULT_PASSTHROUGH=true`, `VEROSIGHT_MODE=mcp`.

**Restart WAJIB:** `.env` dibaca saat startup → `make dev` / restart uvicorn agar model baru aktif.

**Tidak ada perubahan kode yang diperlukan** (terverifikasi):
- `_supports_temperature` (`client.py:401`) cek awalan `gpt-5`; nama `cx/gpt-5.6-terra` diawali `cx/` →
  temperature **tetap dikirim**, dan model menerimanya (teruji). Aman.
- Adapter `chat_completions` memakai `max_tokens` (`client.py:432`); model menerima `max_tokens` (teruji).
- `LLM_REASONING_EFFORT` (opsional) boleh dibiarkan kosong; bila ingin, GPT-5 mendukung effort — uji A/B.

---

## 4. Penyesuaian dengan Progres yang Ada (WAJIB dipahami)

Mesin reliability dibangun untuk **quirk DEEPSEEK**. Dengan model baru, sifatnya bisa berbeda — **jangan
hapus**, cukup verifikasi ulang:

| Mekanisme | Status untuk gpt-5.6-terra |
|---|---|
| `synthesis_mode` + right-size sintesis | **Tetap dipakai** — hemat token/latensi & jaga self-check. Manfaat "cegah empty" mungkin kurang relevan (gpt-5 mungkin tak empty pada konteks besar), tapi tetap menguntungkan. |
| retry-on-empty (`MAX_EMPTY_RETRY`) | **Tetap** sebagai jaring; jarang terpicu bila gpt-5 tak punya quirk empty deepseek. |
| Right-size self-check / status jujur | Tetap. |
| Temuan 9Router (409, cached-empty, 404 transien) | **Level gateway** → mungkin masih berlaku (endpoint sama). Pantau. |

**Prinsip:** ganti model **tanpa** mengubah logika loop. Verifikasi ulang perilaku, bukan menulis ulang.

---

## 5. Gerbang Uji (≥3 run, keyword campuran mis. "nuklir", "keracunan", "MBG")

Karena model berbeda, **verifikasi perilaku nyata**, bukan hanya "200 OK":
- [ ] Run selesai `status=success` dengan jawaban **kaya & akurat** (pakai volume/sentimen/topik).
- [ ] iter-2 sintesis: `synthesis_mode=True`, menghasilkan **teks** → **`"Final answer verified and
      produced"`** (self-check jalan). Perhatikan apakah gpt-5.6-terra pernah empty (idealnya tidak).
- [ ] Pemilihan tool masuk akal (memilih `search_posts` saat perlu pemicu, dst.) — bandingkan kualitas
      penalaran vs deepseek.
- [ ] **Biaya/latensi wajar** — pantau `input_tokens`/`total_tokens` (baseline model ini tinggi). Bila
      biaya melonjak, pertimbangkan menekan `TOOL_RESULT_PASSTHROUGH` atau `max_tokens`.
- [ ] Tak ada error format (400/404 model/`max_tokens`/temperature) di log.

---

## 6. Rollback (revert-if-worse)

Sepele: kembalikan `LLM_MODEL=xkiro/deepseek/deepseek-v4-pro` di `.env` + restart. Tak ada migrasi data,
tak ada perubahan kode → aman. Revert bila: kualitas jawaban turun, biaya/latensi tak wajar, atau muncul
error format yang tak teratasi.

---

## 7. Risiko & Batasan (jujur)

- **RS1 — Biaya/latensi.** Baseline `prompt_tokens` ~2.486 (teruji) jauh di atas input nyata; dengan
  passthrough hasil tool, request bisa mahal. **Pantau ketat** di gerbang uji.
- **RS2 — Perilaku penalaran berbeda.** gpt-5.6-terra bukan deepseek → strategi pemilihan tool, kedalaman,
  gaya jawaban bisa berbeda (bisa lebih baik/buruk). Nilai dari **output nyata ≥3 run**, bukan asumsi.
- **RS3 — Quirk gateway 9Router tetap ada** (409/cached-empty/404 transien; lihat memori proyek) karena
  endpoint sama. Ganti model tak menyembuhkan itu.
- **RS4 — `.env` berisi key asli** — jangan commit; sudah gitignored. Key dipakai sama, tak berubah.
- **Rujukan:** `docs/plan-fix-sintesis-tools-none-proaktif.md`, `docs/plan-fix-retry-on-empty-self-check.md`,
  `docs/plan-fix-restore-synthesis-mode-regression.md`, `docs/PROMPT_PLAN.md` §1.6.
- **Batasan tugas:** dokumen rencana; eksekusi (ubah `.env`, restart, uji ≥3 run, pantau biaya) pada tahap
  terpisah dengan gerbang uji & aturan revert.
