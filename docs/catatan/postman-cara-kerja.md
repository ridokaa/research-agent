# Postman — Cara Kerja Agentic Loop

Catatan penjelasan cara kerja koleksi Postman untuk API **Agentic Loop** yang
terdapat pada folder `docs/postman`.

## Isi Folder `docs/postman`

| File | Peran |
|------|-------|
| `Agentic_Loop_API.postman_collection.json` | Koleksi berisi semua request API Agentic Loop (collection format v2.1.0). |
| `Agentic_Loop_Local.postman_environment.json` | Environment lokal berisi variabel yang dipakai oleh koleksi (misal `baseUrl`). |

## Cara Kerja

1. **Environment sebagai penyimpan variabel**
   - File environment mendefinisikan variabel:
     - `baseUrl` = `http://localhost:8000` (alamat server lokal)
     - `run_id` = kosong, akan diisi otomatis oleh test script
     - `conversation_id` = kosong, akan diisi otomatis oleh test script
   - Di dalam koleksi, variabel dipakai dengan sintaks `{{namaVariabel}}`.
     Contoh: `POST {{baseUrl}}/v1/agent/query`.

2. **Koleksi request dikelompokkan per folder**

   | Folder | Endpoint | Fungsi |
   |--------|----------|--------|
   | **Agent** | `POST /v1/agent/query` | Mengirim pertanyaan ke agent (mode sinkron, jawaban langsung). |
   | | `POST /v1/agent/query/stream` | Sama, tapi jawaban dikirim streaming (SSE `text/event-stream`). |
   | **Runs** | `GET /v1/runs?limit=20&offset=0` | Daftar semua run. |
   | | `GET /v1/runs/:run_id` | Detail satu run. |
   | | `GET /v1/runs/:run_id/trace` | Trace langkah agent (JSON). |
   | | `GET /v1/runs/:run_id/trace?format=text` | Trace dalam format teks. |
   | | `GET /v1/runs/:run_id/tools` | Panggilan tool yang terjadi pada run. |
   | **Conversations** | `GET /v1/conversations?limit=20&offset=0` | Daftar percakapan. |
   | | `GET /v1/conversations/:conversation_id` | Detail satu percakapan. |
   | **Tools** | `GET /v1/tools` | Daftar tool yang aktif. |
   | **System** | `GET /health` | Cek status server. |
   | | `GET /health/deps` | Cek status dependensi. |
   | | `GET /metrics` | Metrik Prometheus. |

3. **Parameter path `:run_id` dan `:conversation_id`**
   - Endpoint seperti `/v1/runs/:run_id` memakai variabel path.
   - Nilainya diambil dari environment (`{{run_id}}`, `{{conversation_id}}`).
   - Alur tipikal:
     1. Jalankan **Query Agent (sync)** → test script otomatis menyimpan
        `run_id` dan `conversation_id` ke environment.
     2. Jalankan **Get Run** / **Get Conversation** → otomatis memakai ID
        hasil langkah 1.

4. **Test script (tab Tests)**
   - Request **Query Agent** menyimpan hasil ke variabel environment:
     - `pm.environment.set('run_id', json.run_id)`
     - `pm.environment.set('conversation_id', json.conversation_id)`
   - Semua request punya assertion dasar, contoh:
     - `pm.response.to.have.status(200)`
     - `pm.expect(json.answer).to.be.a('string')` (di Query Agent)
     - Memastikan header `X-Request-ID` ada.

5. **Alur pakai yang disarankan**
   1. Buka Postman → Import file `.postman_collection.json` dan
      `.postman_environment.json`.
   2. Pilih environment **Agentic Loop — Local**.
   3. Pastikan server berjalan di `http://localhost:8000`.
   4. Kirim `POST /v1/agent/query` terlebih dahulu (men-set `run_id` &
      `conversation_id`).
   5. Kirim request lain (Runs, Conversations, Trace) untuk melihat hasilnya.
