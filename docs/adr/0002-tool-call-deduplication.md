# 2. Deduplikasi Panggilan Tool (*Tool Call Deduplication*) & Caching Hasil

* **Status**: Accepted
* **Tanggal**: 2026-07-30
* **Pengambil Keputusan**: Tim Pengembang Agentic Loop

## Konteks & Masalah

Masalah yang paling sering ditemui pada sistem agen berbasis LLM adalah **loop tak berujung** (*infinite loop*). Hal ini terjadi ketika LLM memanggil sebuah tool dengan argumen tertentu, mendapatkan hasil kosong atau tidak sesuai harapan, lalu pada iterasi berikutnya memanggil tool yang **sama persis** dengan argumen yang **sama persis**. Tanpa mekanisme pencegahan, agen akan terus mengulang pemanggilan tersebut hingga iterasi maksimum habis dan membuang budget kredit secara percuma.

## Keputusan Desain

Kami mengimplementasikan **Deduplikasi Panggilan Tool dan Caching Hasil** pada tingkat `ExecutionMemory` dan `ToolExecutor`:

1. **Pengecekan Cache Identik**:
   Sebelum mengeksekusi panggilan tool baru, `ToolExecutor` memeriksa apakah pasangan `(tool_name, normalized_arguments)` sudah pernah dieksekusi dalam run yang sama.
2. **Pengembalian Hasil Ter-cache & Normalisasi Status**:
   Jika panggilan identik ditemukan, eksekusi I/O ke service/API dihindari. Hasil dari memori eksekusi sebelumnya dikembalikan secara instan dengan kredit 0.
3. **Deteksi No-Progress (Penghenti Loop)**:
   Jika agen melakukan panggilan berulang tanpa perkembangan data baru (*no-progress*), kebijakan penghenti (*policy*) mendeteksi pola ini dan memaksa agen menghentikan loop untuk merangkum jawaban dengan data yang ada.

## Alasan & Pertimbangan

1. **Pencegahan Loop Tak Berujung**:
   Menutup salah satu penyebab kegagalan agen yang paling utama (*infinite repetition loop*).
2. **Efisiensi Kredit & Biaya API**:
   API eksternal berbayar (seperti Verosight) mengenakan biaya kredit per panggilan. Caching memastikan kredit hanya terpakai 1 kali untuk argumen yang sama dalam satu eksekusi run.
3. **Akselerasi Latensi Loop**:
   Eksekusi cache bersifat memori-lokal sehingga memangkas latensi eksekusi dari ratusan milidetik menjadi <1 milidetik.

## Konsekuensi

* **Positif**: Agen lebih tangguh terhadap kegagalan LLM, hemat kredit API, dan terhindar dari pengulangan tak berguna.
* **Negatif**: Jika data API eksternal berubah dalam hitungan milidetik saat run sedang berjalan, agen akan menggunakan data yang tersimpan di cache run tersebut (desain ini memang diharapkan untuk konsistensi eksekusi 1 run).
