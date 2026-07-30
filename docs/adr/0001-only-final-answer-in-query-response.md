# 1. Hanya Jawaban Akhir yang Ditampilkan pada Response API (`/v1/agent/query`)

* **Status**: Accepted
* **Tanggal**: 2026-07-30
* **Pengambil Keputusan**: Tim Pengembang Agentic Loop

## Konteks & Masalah

Pada arsitektur agen riset cerdas (*agentic loop*), agen melakukan serangkaian langkah penalaran (*thinking*), evaluasi konteks, serta eksekusi berbagai tool sebelum menghasilkan jawaban akhir. Pertanyaan mendasar adalah: apakah respons API utama (`POST /v1/agent/query`) harus mengembalikan seluruh pohon eksekusi dan proses berpikir internal, ataukah **hanya jawaban akhir yang terverifikasi** beserta metadata run ringkas?

## Keputusan Desain

Kami memutuskan bahwa `POST /v1/agent/query` **HANYA** mengembalikan jawaban akhir (`answer`) beserta metadata eksekusi tingkat tinggi (`run_id`, `conversation_id`, `status`, `iterations`, `latency_ms`). 

Proses berpikir (*thinking*), evaluasi refleksi, serta log panggilan tool internal **TIDAK PERNAH** dimuat dalam payload respons utama API ini.

## Alasan & Pertimbangan

1. **Abstraksi Antarmuka Pengguna (Clean User Experience)**:
   Aplikasi klien (frontend, widget chat, atau integrasi downstream) umumnya hanya membutuhkan jawaban akhir yang bersih, siap saji, dan akurat. Menyertakan log proses berpikir mentah ke respons umum mengotori antarmuka dan membutuhkan parser khusus di klien.

2. **Keamanan & Efisiensi Konteks (Security & Information Hiding)**:
   Proses berpikir agen dan payload argumen tool dapat mengandung detail infrastruktur internal, token sementara, atau data mentah yang tidak perlu dipaparkan ke pengguna akhir. Membatasi respons hanya pada jawaban final mengurangi risiko kebocoran data sensitif.

3. **Efisiensi Bandwidth & Latensi**:
   Mengirim seluruh riwayat data mentah tool (misalnya 500 post sosial media hasil pencarian) dalam satu payload HTTP akan membengkakkan ukuran paket dari beberapa byte menjadi ratusan kilobyte.

4. **Pemisahan Jalur Observabilitas (Separation of Concerns)**:
   Detail eksekusi dan pohon berpikir agen tetap tersimpan secara lengkap di basis data (`run_steps`). Untuk kebutuhan *debugging*, audit, dan pemantauan real-time, kami menyediakan endpoint observabilitas terpisah:
   - `GET /v1/runs/{run_id}/trace` (JSON pohon trace lengkap)
   - `GET /v1/runs/{run_id}/trace?format=text` (Render pohon teks berwarna)
   - `GET /debug/runs/{run_id}` (HTML visual timeline viewer)

## Konsekuensi

* **Positif**: Respons API sangat ringan, konsisten, aman, dan mudah diintegrasikan oleh sistem lain.
* **Negatif**: Pengembang frontend yang membutuhkan inspeksi eksekusi harus melakukan *fetch* tambahan ke endpoint `/v1/runs/{run_id}/trace`.
