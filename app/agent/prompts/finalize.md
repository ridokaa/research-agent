Tinjau draf jawaban di bawah ini terhadap pertanyaan pengguna, data yang diperoleh, serta daftar tool yang SUDAH dipanggil dan BELUM dipanggil.

Balas HANYA dengan JSON, tanpa teks lain:
{"is_final": true|false, "reason": "<1 kalimat>", "missing": "<nama tool yang perlu dipanggil, atau null>"}

Nilai dengan bijak dan wajar (bukan checklist kaku):
1. KLASIFIKASIKAN dulu jenis pertanyaan:
   - **Faktual sederhana** (mis. 'berapa volume/jumlah X?', 'kapan puncaknya?') → jangan menuntut dimensi ekstra. Bila angka yang diminta sudah ada di data → **`is_final=true`**. JANGAN paksa sentimen/search_posts.
   - **Signifikansi/analisis/opini** (mis. 'apakah X sering dibicarakan?', 'seberapa penting/viral X?', 'bagaimana tanggapan publik?') → bila `get_sentiment` ATAU `search_posts`/`semantic_search` termasuk tool yang BELUM dipanggil dan jelas relevan, set **`is_final=false`** dan sebut tool itu di `missing`.
2. Jika dimensi tambahan tersebut tidak relevan dengan niat pertanyaan pengguna, atau tool-nya sudah pernah dicoba dan kosong/error, atau tidak ada tool yang bisa memberikannya → set is_final=true.
3. Jangan memaksa kedalaman ekstra jika pertanyaan pengguna bersifat sederhana/faktual atau jawaban saat ini sudah memadai secara wajar.

