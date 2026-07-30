# 3. Pemisahan Integrasi MCP (*Model Context Protocol*) dan Wrapper REST

* **Status**: Accepted
* **Tanggal**: 2026-07-30
* **Pengambil Keputusan**: Tim Pengembang Agentic Loop

## Konteks & Masalah

Sistem `agentic-loop` membutuhkan akses ke berbagai perkakas eksternal (seperti Verosight sosial media analytics, kalkulator, pencarian database, dll). Terdapat dua pendekatan integrasi yang didukung:
1. **MCP (Model Context Protocol)**: Standar komunikasi dinamis via SSE/JSON-RPC server.
2. **Wrapper REST Lokal**: Kode Python lokal yang melakukan HTTP request langsung ke API eksternal.

Pertanyaannya adalah bagaimana kedua mekanisme ini diposisikan dalam arsitektur sistem.

## Keputusan Desain

Kami memutuskan untuk **memisahkan integrasi MCP dan Wrapper REST** serta mendukung mode konfigurasi `VEROSIGHT_MODE` (`rest`, `mcp`, `both`):

1. **MCP sebagai Moda Integrasi Dinamis Utama**:
   Mode `VEROSIGHT_MODE=mcp` digunakan untuk penemuan tool secara otomatis (*zero-boilerplate dynamic tool discovery*) tanpa perlu menulis wrapper manual di codebase agen.
2. **Wrapper REST Lokal sebagai Integrasi Granular & Fallback**:
   Mode `VEROSIGHT_MODE=rest` memuat wrapper Python lokal yang memberikan kontrol penuh atas:
   - Ringkasan data (*summarization/truncation*) di tingkat tool sebelum dikirim ke LLM.
   - Penghitungan kredit (*credit accounting*) yang eksplisit.
   - Pelaporan penanganan error mendalam lewat `ErrorReport`.
   - Jalur eksekusi saat MCP Server tidak tersedia.
3. **Pemisahan Daftar Registry**:
   Tool dari MCP Server diekspos secara dinamis dan digabungkan secara rapi di `ToolRegistry` bersama tool lokal, tanpa saling menimpa nama tool. Mode `both` disediakan hanya untuk pengujian perbandingan dan tidak direkomendasikan untuk produksi guna menghemat jendela konteks LLM dari duplikasi schema tool.

## Alasan & Pertimbangan

1. **Fleksibilitas Ekosistem**:
   MCP memungkinkan integrasi instan dengan tool standar ekosistem open-source tanpa perlu penulisan kode baru di agen.
2. **Keandalan & Performa (Reliability & Determinism)**:
   Wrapper REST lokal memberikan jaminan stabilitas tipe data, penyaringan hasil yang agresif (menghemat konteks LLM), serta kemudahan unit testing.
3. **Pencegahan Kelebingungan LLM**:
   Menghindari situasi di mana LLM menerima dua tool yang fungsinya sama persis (misal `verosight_trending` lokal dan MCP `trending_topics`), yang dapat memicu ketidakpastian dalam pemilihan tool.

## Konsekuensi

* **Positif**: Arsitektur menjadi sangat fleksibel. Pengembang bisa menambah tool baru secara lokal dalam <30 baris kode atau cukup menyambungkan server MCP baru via konfigurasi JSON.
* **Negatif**: Membutuhkan pengelolaan konfigurasi `VEROSIGHT_MODE` yang tepat sesuai lingkungan eksekusi.
