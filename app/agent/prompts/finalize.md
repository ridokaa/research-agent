Tinjau draf jawaban di bawah ini terhadap pertanyaan pengguna, data yang diperoleh, serta daftar tool yang SUDAH dipanggil dan BELUM dipanggil.

Balas HANYA dengan JSON, tanpa teks lain:
{"is_final": true|false, "reason": "<1 kalimat>", "missing": "<nama tool yang perlu dipanggil, atau null>"}

Nilai dengan bijak dan wajar (bukan checklist kaku):
1. set is_final=false HANYA JIKA ada dimensi penting yang jelas relevan dengan pertanyaan, belum terjawab, dan berharga untuk diperoleh dari tool yang BELUM dipanggil (dan tool tersebut belum pernah dicoba).
2. Jika dimensi tambahan tersebut tidak relevan dengan niat pertanyaan pengguna, atau tool-nya sudah pernah dicoba dan kosong/error, atau tidak ada tool yang bisa memberikannya → set is_final=true.
3. Jangan memaksa kedalaman ekstra jika pertanyaan pengguna bersifat sederhana/faktual atau jawaban saat ini sudah memadai secara wajar.

