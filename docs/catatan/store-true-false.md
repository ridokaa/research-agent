# Catatan Penggunaan `store: true` dan `store: false`

Catatan tentang bagaimana opsi `store` bekerja pada project Agentic Loop.
Fokus pada perbedaan perilaku, di mana ia diatur, dan kapan harus memakainya.

## Apa itu `store`?

`store` adalah opsi pada `RunOptions` (app/schemas/agent.py:21) yang menentukan
apakah server LLM menyimpan state percakapan (server-side state) selama satu run.

- `store: true`  → memakai **Responses API** dengan `previous_response_id`.
- `store: false` → **tidak ada state di server**, seluruh riwayat harus dikirim
  ulang (replay) setiap iterasi.

Default: `True` (dari `LLM_STORE` di app/config.py:56, bisa di-override via body
request API pada field `options.store`).

## Perbedaan Perilaku

| Aspek | `store: true` | `store: false` |
|-------|---------------|----------------|
| State percakapan | Disimpan di server LLM | Harus direplay oleh client |
| Field payload | `"store": true` + `previous_response_id` | `"store": false`, tanpa `previous_response_id` |
| Isi `input` setiap iterasi | Hanya item baru (`pending_inputs`) | Full history + `pending_inputs`, di-compact |
| Ukuran request | Kecil & hemat token | Besar (riwayat penuh) |
| Kompatibilitas API | Hanya **Responses API** (`POST /responses`) | Responses API & Chat Completions |

## Cara Kerja di Dalam Kode

### 1. Penentuan nilai `store`

Di `app/agent/loop.py:79`, nilai awal diambil dari `settings.LLM_STORE`,
kemudian bisa di-override oleh `RunOptions(options.store)` yang dikirim lewat API.

Jika `api_style == "chat_completions"`, `store` **dipaksa menjadi `false`**
karena endpoint chat/completions tidak mendukung `store`:

```python
# app/agent/loop.py:82-84
if effective_api_style == "chat_completions" and opts.store:
    opts = opts.model_copy(update={"store": False})
```

### 2. Pembentukan request (planner)

Di `app/agent/planner.py:78-88`, pada iterasi lanjutan:

- `store=True` → hanya kirim `pending_inputs` (item baru) sebagai `input`.
  Konteks sebelumnya diambil server via `previous_response_id`.
- `store=False` → bangun `history_items` dari `user_query` + `pending_inputs`,
  lalu di-compact dengan `compact_input(..., keep_last_n=10)` agar tidak
  melebihi batas konteks.

### 3. Pengiriman ke LLM (client)

Di `app/llm/client.py`:

- Payload Responses API menyertakan `"store": self._store` (client.py:441).
- `previous_response_id` hanya dikirim jika `store=True`
  (app/agent/loop.py:223).
- Jika `store=False` dan input berupa list berisi lebih dari 20 item,
  client meng-compact lagi ke 10 item terakhir (client.py:225-226).
- Untuk `chat_completions`, field `store`/`previous_response_id` **tidak**
  disertakan sama sekali (client.py:420-435).

### 4. Penggunaan `previous_response_id`

- Setiap respons LLM menyimpan `response_id` ke `state.last_response_id`
  (app/agent/loop.py:273).
- Pada iterasi berikutnya, ID ini dikirim sebagai `previous_response_id` agar
  server tahu lanjutan dari respons sebelumnya — hanya berlaku jika `store=True`.

## Kapan Memakai `store: true` / `false`

**`store: true` (rekomendasi default):**
- Memakai model/endpoint yang mendukung Responses API.
- Menghemat token dan bandwidth karena tidak perlu mengirim ulang riwayat.
- Berguna untuk run dengan banyak iterasi/tool call (konteks panjang).

**`store: false`:**
- Memakai `api_style = "chat_completions"` (dipaksa otomatis).
- Ingin state benar-benar stateless / tidak menyimpan data di server LLM.
- Perlu kompatibilitas lintas penyedia (provider yang tidak mendukung
  Responses API).

> Catatan: pada mode `store=false`, riwayat panjang akan di-compact
> (`compact_input`, keep_last_n=10). Jika run sangat panjang, konteks awal
> mungkin terpotong/summarize.

## Contoh Request API

```json
{
  "query": "Apa tren di Verosight minggu ini?",
  "conversation_id": null,
  "options": {
    "store": true,
    "max_iterations": 8,
    "credits_budget": 100
  }
}
```

Ubah `"store": false` pada `options` untuk menjalankan mode stateless.

## Referensi Kode

- `app/schemas/agent.py:21` — definisi `RunOptions.store`.
- `app/config.py:56` — setting `LLM_STORE` (default `True`).
- `app/agent/loop.py:79,83-84,142,199,223` — alur pemakaian `store`.
- `app/agent/planner.py:51-88` — pembentukan payload `store`/compact.
- `app/llm/client.py:111-112,127,225-226,274,441` — kirim `store` ke API.
- `app/llm/schemas.py:367-404` — `compact_input` untuk mode `store=false`.
- `tests/test_agent_loop.py:222-233` — test: chat_completions memaksa `store=false`.
- `tests/test_llm_client.py:438-450,927-928` — test payload `store`.
