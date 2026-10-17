# Rencana — Uji Model Berbeda (gpt-4o) & Penyaringan Hasil per-Lokasi (mis. Indonesia)

> **Dokumen rencana untuk agen eksekutor.** Tujuan: menyelesaikan masalah "agen mengambil data
> asing, bukan Indonesia" dengan (1) menaikkan model ke gpt-4o, lalu (2) bila perlu, memperkuat
> system prompt agar model menyaring per-lokasi. Audit pada branch
> `retrofit/chat-completions-adapter` (2026-08-03). Patuhi aturan global `docs/PROMPT_PLAN.md`
> §1.6 (tanpa mock, transparansi error).

---

## 1. Ringkasan Masalah (yang sudah dikonfirmasi lewat trace)

- Query: *"Apa topik yang sedang tren di Indonesia minggu lalu?"* → run `success`, 7 iterasi,
  ~90 detik, tetapi jawaban "data terbatas / bukan Indonesia".
- **Tools BEKERJA.** Trace menunjukkan agen memanggil `verosight__get_topics` (tool yang tepat),
  status `success`.
- **Akar masalah:** model memanggil `get_topics` dengan `keyword: ""` (kosong) → Verosight
  mengembalikan **sampel global** (retweet Portugis/Brasil, berita India + Hindi), bukan Indonesia.
- **Bukti dataset bisa disaring:** dengan model LLM lain (deepseek/qwen/gpt-5 via 9Router),
  hasil sebelumnya "cukup sesuai" dan bisa memilah Indonesia. Artinya perbedaannya ada pada
  **kualitas tool-use model**, bukan bug aplikasi.
- **Batasan penting:** tool Verosight **tidak punya filter `country`/`region` yang keras**.
  Penyaringan ke Indonesia terjadi via **`keyword` / `semantic_search`** pada teks/konteks
  Indonesia — hasilnya "cukup sesuai", bukan pagar geografis sempurna. Verosight juga sumber data
  **sosial**, bukan cuaca.

---

## 2. Perubahan yang SUDAH Diterapkan (titik awal plan ini)

Di `.env`, model aktif dinaikkan dari `gpt-4o-mini` → **`gpt-4o`** (baris `LLM_MODEL`). Konfigurasi
LLM lain tidak diubah dan sudah kompatibel:

```dotenv
LLM_API_STYLE=responses
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o            # sebelumnya gpt-4o-mini
LLM_MAX_OUTPUT_TOKENS=4096  # < batas 16384 gpt-4o → tidak memicu 400
LLM_REASONING_EFFORT=       # kosong → tidak mengirim reasoning.effort (benar untuk gpt-4o)
LLM_STORE=true
```

Alasan: gpt-4o jauh lebih baik dalam pemilihan tool & pengisian argumen (mis. mengisi `keyword`
atau memilih `semantic_search`), sehingga lebih mungkin menyaring ke Indonesia daripada
gpt-4o-mini.

---

## 3. Tahap A — Uji gpt-4o (verifikasi apakah penyaringan membaik)

**A1. Muat ulang konfigurasi.** `get_settings()` di-cache saat startup → **restart server**:
```bash
# hentikan proses lama (Ctrl-C), lalu:
make dev            # uvicorn app.main:app --reload
curl -s http://localhost:8000/health/deps    # pastikan db/llm/mcp sehat
```
Konfirmasi model aktif benar `gpt-4o`:
```bash
curl -s http://localhost:8000/health/deps | grep -o 'gpt-4o[a-z-]*'   # harus "gpt-4o"
```

**A2. Kirim ulang query yang sama** (Postman `POST {{baseUrl}}/v1/agent/query`):
```json
{ "query": "Apa topik yang sedang tren di Indonesia minggu lalu?",
  "conversation_id": null,
  "options": { "max_iterations": 8, "credits_budget": 100 } }
```
Simpan `run_id` dari response.

**A3. Periksa trace** (`GET {{baseUrl}}/v1/runs/{run_id}/trace?format=text`) — fokus pada event
`tool_selected` dan argumennya:
- ✅ **Berhasil** bila gpt-4o memanggil `verosight__semantic_search` / `verosight__search_posts`
  dengan query memuat "Indonesia", **atau** `get_topics` / `get_trending` dengan
  `keyword: "Indonesia"` (bukan `""`).
- ❌ **Belum** bila `keyword` masih kosong dan hasil tetap global.

**A4. Periksa data** (`GET {{baseUrl}}/v1/runs/{run_id}/tools`) → cek apakah `result` kini memuat
konten berbahasa/berkonteks Indonesia.

**Kriteria lulus Tahap A:** jawaban akhir relevan dengan Indonesia **dan** trace menunjukkan
argumen penyaringan (`keyword`/query "Indonesia") terisi. Jika lulus → lanjut ke Tahap D
(verifikasi akhir). Jika **tidak** lulus → lanjut ke Tahap B.

---

## 4. Tahap B — Perkuat System Prompt (bila gpt-4o pun belum menyaring)

**File:** `app/agent/prompts/system.md`
**Masalah prompt saat ini:** generik; hanya menyarankan "ganti kata kunci/platform" tanpa
memerintahkan model mengekstrak lokasi/topik dari pertanyaan dan meneruskannya ke tool.

**Perubahan yang direncanakan (tambah bagian instruksi, jangan hapus aturan yang ada):**
tambahkan blok panduan pemakaian tool, mis.:

> ## Panduan memakai tool pencarian
> - Ekstrak **lokasi, topik, dan rentang waktu** dari pertanyaan pengguna. Jika pengguna
>   menyebut lokasi (mis. "Indonesia"), **wajib** teruskan sebagai `keyword`/`query` ke tool —
>   jangan biarkan `keyword` kosong.
> - Untuk permintaan bertipe "cari/apa yang dibahas/topik tentang X di <lokasi>", **utamakan**
>   `semantic_search` atau `search_posts` dengan query natural yang memuat lokasi & topik;
>   `get_topics`/`get_trending` hanya untuk tren umum tanpa penyaringan.
> - Jika hasil pertama tampak tidak sesuai lokasi (mis. berbahasa/berkonteks negara lain),
>   ulangi dengan `keyword` lokasi yang lebih eksplisit sebelum menyerah.
> - Verosight adalah data **sosial** (postingan), bukan sumber fakta real-time seperti cuaca;
>   jawab berdasarkan apa yang diposting orang, dan sebutkan keterbatasan itu bila relevan.

**Catatan penting:** ini mengedit **konten prompt**, bukan inti loop — arsitektur tetap
style-agnostic. Jangan menaruh logika penyaringan di `app/agent/` atau membuat filter geografis
palsu; penyaringan tetap dilakukan model lewat argumen tool nyata.

**Uji ulang:** ulangi Tahap A2–A4. Kriteria lulus sama.

---

## 5. Tahap C — Kontingensi: kembali ke model 9Router yang terbukti (opsional)

Jika gpt-4o + prompt yang diperkuat masih kurang memuaskan dibanding pengalaman sebelumnya,
kembalikan ke model yang dulu terbukti bisa memilah Indonesia (deepseek/qwen). **Bukan** sekadar
uncomment `LLM_MODEL` — model 9Router butuh endpoint & gaya berbeda:

```dotenv
LLM_API_STYLE=chat_completions
LLM_BASE_URL=https://9router.fhs.my.id/v1
LLM_API_KEY=<KUNCI_9ROUTER_ANDA>          # bukan kunci OpenAI
LLM_MODEL=xkiro/deepseek/deepseek-v4-pro   # atau xkiro/qwen/qwen3.7-max
```
Restart server, ulangi Tahap A2–A4. Client sudah mendukung gaya `chat_completions` (retrofit),
jadi loop tetap style-agnostic. **Prasyarat:** Anda memiliki kunci 9Router yang valid.

---

## 6. Tahap D — Verifikasi Akhir

- [ ] `health/deps` menampilkan `llm` = configured `gpt-4o` (atau model 9Router bila Tahap C).
- [ ] Trace `tool_selected` menunjukkan argumen penyaringan terisi (`keyword`/query "Indonesia"),
      bukan `keyword: ""`.
- [ ] `GET /v1/runs/{run_id}/tools` → `result` memuat konten berkonteks Indonesia.
- [ ] Jawaban akhir relevan dengan Indonesia (atau menyatakan jujur bila dataset memang tak punya
      data Indonesia untuk periode itu — bukan mengarang).
- [ ] Tidak ada regresi 500 (rujuk `docs/plan-fix-500-agent-query.md` bila muncul lagi).
- [ ] Rahasia (`api_key`) tetap teredaksi di log/trace/response.
- [ ] Biaya per-panggilan dipantau (gpt-4o lebih mahal dari gpt-4o-mini; gunakan
      `credits_budget`/`max_iterations` wajar saat menguji).

---

## 7. Pohon Keputusan (ringkas untuk agen eksekutor)

```
Restart server (gpt-4o)
   └─ uji query Indonesia + baca trace
        ├─ keyword terisi & hasil Indonesia  → SELESAI (Tahap D)
        └─ keyword masih kosong / hasil global
             └─ perkuat system.md (Tahap B) → uji lagi
                  ├─ membaik → SELESAI (Tahap D)
                  └─ masih kurang → kembali ke 9Router deepseek/qwen (Tahap C, butuh kunci)
                       └─ uji lagi → SELESAI (Tahap D)
```

---

## 8. Catatan & Batasan

- **Bukan bug aplikasi.** Aplikasi meneruskan apa adanya isi Verosight; `status:success` = panggilan
  API berhasil, bukan jaminan data sesuai maksud. Fokus perbaikan = kualitas tool-use model +
  panduan prompt.
- **Tidak ada filter geografis keras** di tool Verosight; penyaringan Indonesia bersifat
  keyword/semantik → hasil "cukup sesuai", bukan presisi geografis. Jangan merekayasa filter
  geografis palsu di aplikasi (langgar §1.6).
- **Cakupan dataset akun Verosight** tetap menentukan batas atas; jika suatu periode memang tak
  punya data Indonesia, jawaban jujur "tidak ada data" adalah hasil yang benar.
- **Verosight = data sosial, bukan cuaca/fakta real-time.** Kelola ekspektasi jenis pertanyaan.
- **Rujukan silang:** `docs/PROMPT_PLAN.md` (§1.3–§1.6 kontrak & aturan global);
  `docs/Plan-perubahan-model.md` (sinkronisasi konfigurasi model OpenAI);
  `docs/plan-fix-500-agent-query.md` (jika 500 muncul); `docs/plan-perubahan-cara-kerja.md`
  (pass-through data penuh, bila hasil tool terasa terpangkas).
- **Batasan tugas:** dokumen rencana; eksekusi (restart, uji, edit prompt, commit) dilakukan pada
  tahap terpisah oleh agen/developer.
```
