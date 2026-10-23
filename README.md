# 📱 All-in-One Media Downloader Telegram Bot

Ushbu bot Python dasturlash tili, zamonaviy **aiogram 3.x**, **yt-dlp** hamda **FFmpeg** yordamida yaratilgan.

---

## ✨ Imkoniyatlar

1. **📸 Instagram:**
   * Reels, Postlar va videolarni eng yuqori tezlikda yuklab beradi.
2. **▶️ YouTube va YouTube Shorts (Format va sifat tanlash imkoni bilan):**
   * YouTube havolasi yuborilganda interaktiv menyu ochiladi:
     * 📹 **720p HD** — Yuqori sifatli video
     * 📹 **480p** — O'rtacha sifat
     * 📹 **360p** — Tezkor va kam joy egallaydigan sifat
     * 🎵 **Audio (MP3)** — Videoning faqat musiqasini/ovozini alohida MP3 qilib yuklab berish!
3. **📌 Pinterest:**
   * Rasmlarni original sifatda (`originals`) 1 soniyada yuklab beradi.
   * Video pinlarni ham qo'llab-quvvatlaydi.
4. **🔒 Majburiy kanal obunasi:**
   * Foydalanuvchilar kanalingizga a'zo bo'lmaguncha botdan foydalana olmaydi.

---

## 🚀 Ishga tushirish

```powershell
# 1. Virtual muhitni yoqish
.\venv\Scripts\activate

# 2. FFmpeg yo'lini qo'shish va botni ishga tushirish
$ffmpegDir = "C:\Users\lenovo\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"; $env:PATH = "$ffmpegDir;$env:PATH"; python main.py
```
