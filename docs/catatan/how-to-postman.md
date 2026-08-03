# How to Postman

Catatan tentang cara menggunakan dan menjalankan Postman.

## Apa itu Postman?

Postman adalah alat (tool) untuk menguji API. Dengan Postman kita bisa mengirim
request HTTP (GET, POST, PUT, DELETE, dll) ke suatu endpoint/server, lalu melihat
response yang dikembalikan tanpa perlu menulis kode.

## Cara Menjalankan Postman

1. **Instalasi** - Unduh dan instal Postman dari situs resminya, lalu buka aplikasinya.
2. **Buat Request Baru**
   - Klik tombol **New** lalu pilih **HTTP Request**.
   - Atau di tab Collections, klik **+** untuk membuat request baru.
3. **Atur Metode HTTP**
   - Di sebelah kiri kolom URL, terdapat dropdown untuk memilih metode (GET, POST, PUT, DELETE, dll).
4. **Isi URL**
   - Masukkan URL endpoint, contoh: `https://api.example.com/users`.
5. **Kirim Request**
   - Klik tombol **Send**. Hasil (status code, headers, body) akan tampil di bawah.

## Fitur-fitur Penting

| Fitur | Fungsi |
|-------|--------|
| **Params** | Menambahkan query parameter ke URL. |
| **Headers** | Menambah/mengedit header request (misal `Authorization`, `Content-Type`). |
| **Body** | Mengisi isi request untuk metode POST/PUT, biasa untuk JSON. |
| **Authorization** | Mengatur autentikasi (Bearer token, Basic Auth, OAuth, dll). |
| **Collections** | Mengelompokkan beberapa request agar bisa dijalankan & dikelola bersama. |
| **Tests** | Menulis skrip (JavaScript) untuk menambahkan assertion pada response. |

## Alur Kerja Umum

1. Buat request di Postman (metode + URL + body/header).
2. Klik **Send** untuk melihat respons.
3. Simpan request ke dalam Collection agar bisa dipakai ulang.
4. (Opsional) Gunakan **Runner** untuk menjalankan Collection dengan banyak request sekaligus.
5. (Opsional) Tambahkan **Tests** untuk memastikan status code, isi JSON, dst.