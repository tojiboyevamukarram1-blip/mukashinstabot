import asyncio
import logging
import os
import sys
import time
import uuid
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramEntityTooLarge, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import yt_dlp.utils

from config import BOT_TOKEN, CHANNELS
from downloader import (
    detect_platform,
    download_media,
    download_youtube_audio,
    download_youtube_video,
    get_youtube_info,
)
from subscription import (
    SubscriptionMiddleware,
    build_subscription_keyboard,
    get_unsubscribed_channels,
)

# Logging sozlash
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Dispatcher yaratish
dp = Dispatcher()

# Majburiy obunani tekshiruvchi middleware'larni ulash
dp.message.outer_middleware(SubscriptionMiddleware())
dp.callback_query.outer_middleware(SubscriptionMiddleware())

# Bot username kesh
BOT_USERNAME: str = "mukashinstabot"

PLATFORM_INFO = {
    "instagram": {"emoji": "📸", "name": "Instagram"},
    "youtube":   {"emoji": "▶️", "name": "YouTube"},
    "pinterest": {"emoji": "📌", "name": "Pinterest"},
}

# YouTube havolalari uchun vaqtinchalik xotira keshi
YOUTUBE_CACHE: dict = {}


def _cleanup_yt_cache():
    """1 soatdan eski kesh yozuvlarini tozalash."""
    now = time.time()
    expired = [k for k, v in YOUTUBE_CACHE.items() if now - v.get("created_at", 0) > 3600]
    for k in expired:
        YOUTUBE_CACHE.pop(k, None)


async def safe_remove(file_path: str, retries: int = 5, delay: float = 1.0):
    """Windows'da fayl band bo'lib qolgan holatda xavfsiz o'chirish."""
    if not file_path:
        return
    for _ in range(retries):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        except PermissionError:
            await asyncio.sleep(delay)
        except Exception as err:
            logger.warning(f"Faylni o'chirishda ogohlantirish: {err}")
            break


@dp.callback_query(F.data == "check_subscription")
async def handle_check_subscription(callback: CallbackQuery, bot: Bot):
    """Foydalanuvchi 'Obunani tekshirish' tugmasini bosganda ishlaydi."""
    unsubscribed = await get_unsubscribed_channels(bot, callback.from_user.id)
    if not unsubscribed:
        await callback.answer("✅ Obunangiz muvaffaqiyatli tasdiqlandi!")
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            f"✅ <b>Assalomu alaykum, {callback.from_user.first_name}!</b>\n\n"
            "Kanalimizga a'zo bo'lganingiz uchun rahmat!\n"
            "Endi menga istalgan Instagram, YouTube yoki Pinterest havolasini yuborishingiz mumkin. 🚀",
            parse_mode="HTML"
        )
    else:
        await callback.answer(
            "❌ Siz hali kanalga a'zo bo'lmadingiz! Iltimos, avval obuna bo'ling.",
            show_alert=True
        )
        keyboard = build_subscription_keyboard(unsubscribed)
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass


@dp.callback_query(F.data.startswith("yt:"))
async def handle_youtube_selection(callback: CallbackQuery, bot: Bot):
    """YouTube format va sifatini tanlash tugmasi bosilganda ishlaydi."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri buyruq")
        return

    _, cache_id, action = parts

    if action == "cancel":
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer("Bekor qilindi")
        return

    data = YOUTUBE_CACHE.get(cache_id)
    if not data:
        await callback.answer("⚠️ Ushbu havola eskirgan. Iltimos, havolani qaytadan yuboring.", show_alert=True)
        return

    url = data["url"]
    title = data.get("title", "YouTube")
    duration = data.get("duration")

    # 1. AUDIO (MP3) TANLANGAN BO'LSA
    if action == "mp3":
        await callback.message.edit_text("⏳ 🎵 <b>Audio (MP3)</b> yuklab olinmoqda, iltimos kuting...", parse_mode="HTML")
        await bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.RECORD_VOICE)

        downloaded_file = None
        try:
            downloaded_file, info = await download_youtube_audio(url)
            if not downloaded_file or not os.path.exists(downloaded_file):
                await callback.message.edit_text("❌ Audioni yuklab olishning imkoni bo'lmadi.")
                return

            if os.path.getsize(downloaded_file) > 50 * 1024 * 1024:
                await callback.message.edit_text(
                    "⚠️ Audio fayl hajmi 50 MB dan katta bo'lgani sababli Telegram uni qabul qilmadi.\n"
                    "Iltimos, qisqaroq video tanlang."
                )
                return

            caption = f"🎵 {title}\n📥 @{BOT_USERNAME} orqali yuklab olindi"
            file_input = FSInputFile(downloaded_file)

            await callback.message.reply_audio(
                audio=file_input,
                title=title,
                performer=data.get("uploader", "YouTube"),
                duration=int(duration) if duration else None,
                caption=caption,
                request_timeout=300
            )
            await callback.message.delete()

        except TelegramEntityTooLarge as e:
            logger.error(f"Telegram audio hajmi xatosi: {e}")
            await callback.message.edit_text(
                "⚠️ Audio hajmi juda katta bo'lgani sababli Telegram uni qabul qilmadi."
            )
        except TelegramNetworkError as e:
            logger.error(f"Telegram audio tarmoq xatosi: {e}")
            await callback.message.edit_text(
                "⚠️ Tarmoq xatoligi yuz berdi. Iltimos, qaytadan urinib ko'ring."
            )
        except Exception as e:
            logger.exception(f"YouTube audio yuklashda xatolik: {e}")
            await callback.message.edit_text("❌ Audio faylni yuklab olishda xatolik yuz berdi.")
        finally:
            if downloaded_file:
                await safe_remove(downloaded_file)

    # 2. VIDEO SIFATI (720p, 480p, 360p) TANLANGAN BO'LSA
    elif action in ["720", "480", "360"]:
        quality_label = f"{action}p"
        await callback.message.edit_text(
            f"⏳ 1/2: 📹 <b>{quality_label}</b> video yuklab olinmoqda, iltimos kuting...",
            parse_mode="HTML"
        )
        await bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_VIDEO)

        downloaded_file = None
        try:
            downloaded_file, info = await download_youtube_video(url, quality=action)
            if not downloaded_file or not os.path.exists(downloaded_file):
                await callback.message.edit_text("❌ Videoni yuklab olishning imkoni bo'lmadi.")
                return

            file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
            await callback.message.edit_text(
                f"⚡️ 2/2: Telegram'ga yuborilmoqda ({file_size_mb:.1f} MB)...",
                parse_mode="HTML"
            )

            caption = f"▶️ YouTube ({quality_label}) | @{BOT_USERNAME} orqali yuklab olindi"
            file_input = FSInputFile(downloaded_file)

            await callback.message.reply_video(
                video=file_input,
                caption=caption,
                duration=int(duration) if duration else None,
                supports_streaming=True,
                request_timeout=300
            )
            await callback.message.delete()

        except Exception as e:
            logger.exception(f"YouTube video yuklashda xatolik ({action}): {e}")
            await callback.message.edit_text("❌ Videoni yuklab olishda xatolik yuz berdi.")
        finally:
            if downloaded_file:
                await safe_remove(downloaded_file)


@dp.message(CommandStart())
async def handle_start(message: Message):
    """Start komandasi uchun handler."""
    welcome_text = (
        f"Assalomu alaykum, <b>{message.from_user.first_name}</b>! 👋\n\n"
        "Men <b>Media Downloader</b> botiman.\n\n"
        "Menga quyidagi platformalardagi havolalarni yuboring:\n\n"
        "📸 <b>Instagram</b> — Reels, Post, IGTV videolari\n"
        "▶️ <b>YouTube</b> — Videolar, Shorts (video sifati yoki MP3 tanlash bilan)\n"
        "📌 <b>Pinterest</b> — Rasmlar va pinlar\n\n"
        "<i>Menga faqat havolani (linkni) yuboring!</i>"
    )
    await message.answer(welcome_text, parse_mode="HTML")


@dp.message(Command("help"))
async def handle_help(message: Message):
    """Help komandasi uchun handler."""
    help_text = (
        "📖 <b>Botdan foydalanish yo'riqnomasi:</b>\n\n"
        "1. Kerakli kontentni (video yoki rasm) oching va havolasini nusxalang.\n"
        "2. Nusxalangan havolani shu botga yuboring.\n"
        "3. Agar YouTube havolasi bo'lsa, sifat (720p, 480p, 360p) yoki Audio (MP3) ni tanlang.\n"
        "4. Bot media faylni yuklab, sizga taqdim etadi!\n\n"
        "<b>Qo'llab-quvvatlanadigan platformalar:</b>\n"
        "📸 Instagram — Reels, Postlar\n"
        "▶️ YouTube — Videolar, Shorts (Video / MP3)\n"
        "📌 Pinterest — Rasmlar va pinlar\n"
    )
    await message.answer(help_text, parse_mode="HTML")


@dp.message(F.text)
async def handle_media_link(message: Message, bot: Bot):
    """Foydalanuvchi yuborgan xabardagi media havolasini ushlab olish."""
    result = detect_platform(message.text)

    # Agar xabarda tanilgan havola topilmasa
    if not result:
        await message.reply(
            "⚠️ Iltimos, to'g'ri havola yuboring.\n\n"
            "Qo'llab-quvvatlanadigan platformalar:\n"
            "📸 Instagram — <code>https://www.instagram.com/reel/...</code>\n"
            "▶️ YouTube — <code>https://www.youtube.com/watch?v=...</code>\n"
            "📌 Pinterest — <code>https://www.pinterest.com/pin/...</code>",
            parse_mode="HTML"
        )
        return

    platform, url = result

    # ==================== YOUTUBE FORMAT TANLASH ====================
    if platform == "youtube":
        status_msg = await message.reply("⏳ 🔍 YouTube video ma'lumotlari olinmoqda...")
        try:
            info = await get_youtube_info(url)
            title = info.get('title', 'YouTube Video')
            duration = info.get('duration')

            if duration:
                minutes, seconds = divmod(int(duration), 60)
                hours, minutes = divmod(minutes, 60)
                if hours > 0:
                    dur_str = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    dur_str = f"{minutes}:{seconds:02d}"
            else:
                dur_str = "Noma'lum"

            cache_id = uuid.uuid4().hex[:8]
            _cleanup_yt_cache()
            YOUTUBE_CACHE[cache_id] = {
                "url": url,
                "title": title,
                "duration": duration,
                "uploader": info.get('uploader', 'YouTube'),
                "created_at": time.time(),
            }

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📹 720p HD", callback_data=f"yt:{cache_id}:720"),
                    InlineKeyboardButton(text="📹 480p ⚡️ Tezkor", callback_data=f"yt:{cache_id}:480"),
                ],
                [
                    InlineKeyboardButton(text="📹 360p 🚀 Juda tez", callback_data=f"yt:{cache_id}:360"),
                    InlineKeyboardButton(text="🎵 Audio MP3 ⚡️", callback_data=f"yt:{cache_id}:mp3"),
                ],
                [
                    InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"yt:{cache_id}:cancel"),
                ]
            ])

            text = (
                f"🎬 <b>{title}</b>\n"
                f"⏱ <b>Davomiyligi:</b> {dur_str}\n\n"
                f"👇 <b>Qaysi format va sifatda yuklab olmoqchisiz?</b>"
            )
            await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            return
        except Exception as e:
            logger.error(f"YouTube info olishda xatolik: {e}")
            await status_msg.edit_text("❌ YouTube ma'lumotlarini olishda xatolik yuz berdi. Havolani tekshirib ko'ring.")
            return

    # ==================== INSTAGRAM VA PINTEREST TEZKOR YUKLASH ====================
    pinfo = PLATFORM_INFO.get(platform, {"emoji": "📥", "name": platform.capitalize()})

    status_msg = await message.reply(
        f"⏳ {pinfo['emoji']} <b>{pinfo['name']}</b> dan yuklab olinmoqda, iltimos kuting...",
        parse_mode="HTML"
    )

    if platform == "pinterest":
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
    else:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO)

    downloaded_file = None
    try:
        downloaded_file, info = await download_media(platform, url)

        if not downloaded_file or not os.path.exists(downloaded_file):
            await status_msg.edit_text("❌ Media faylni yuklab olishning imkoni bo'lmadi.")
            return

        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        if "_compressed" in downloaded_file:
            await status_msg.edit_text(
                f"📦 Video siqildi ({file_size_mb:.1f} MB). Yuborilmoqda..."
            )

        file_size = os.path.getsize(downloaded_file)
        max_size = 50 * 1024 * 1024
        if file_size > max_size:
            await status_msg.edit_text(
                "⚠️ Fayl hajmi 50 MB dan katta bo'lgani sababli yuborib bo'lmadi."
            )
            return

        ext_lower = downloaded_file.lower()
        is_video = ext_lower.endswith(('.mp4', '.mkv', '.webm', '.mov'))
        is_image = ext_lower.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))

        caption = f"{pinfo['emoji']} {pinfo['name']} | @{BOT_USERNAME} orqali yuklab olindi"
        file_input = FSInputFile(downloaded_file)

        if is_video:
            duration = info.get('duration') if info else None
            width = info.get('width') if info else None
            height = info.get('height') if info else None

            await message.reply_video(
                video=file_input,
                caption=caption,
                duration=int(duration) if duration else None,
                width=int(width) if width else None,
                height=int(height) if height else None,
                supports_streaming=True,
                request_timeout=300
            )
        elif is_image:
            sent = False
            if info and isinstance(info, dict) and info.get('direct_url'):
                try:
                    await message.reply_photo(
                        photo=info['direct_url'],
                        caption=caption,
                        request_timeout=30
                    )
                    sent = True
                except Exception:
                    sent = False

            if not sent:
                await message.reply_photo(
                    photo=file_input,
                    caption=caption,
                    request_timeout=60
                )
        else:
            await message.reply_document(
                document=file_input,
                caption=caption,
                request_timeout=120
            )

        await status_msg.delete()

    except yt_dlp.utils.DownloadError as e:
        error_str = str(e).lower()
        logger.error(f"yt-dlp xatoligi ({platform}): {e}")
        if "private" in error_str or "login" in error_str:
            await status_msg.edit_text(
                f"❌ Bu {pinfo['name']} kontenti yopiq (private) bo'lgani uchun yuklab bo'lmadi."
            )
        elif "not found" in error_str or "404" in error_str:
            await status_msg.edit_text(
                f"❌ Havola bo'yicha {pinfo['name']} da hech qanday kontent topilmadi yoki u o'chirilgan."
            )
        else:
            await status_msg.edit_text(
                "❌ Yuklab olishda xatolik yuz berdi. Havolani tekshirib qayta urinib ko'ring."
            )
    except TelegramEntityTooLarge as e:
        logger.error(f"Telegram fayl hajmi xatoligi ({platform}): {e}")
        await status_msg.edit_text(
            "⚠️ Video hajmi 50 MB dan katta bo'lgani sababli Telegram uni qabul qilmadi."
        )
    except TelegramNetworkError as e:
        logger.error(f"Telegram tarmoq xatoligi / timeout ({platform}): {e}")
        await status_msg.edit_text(
            "⚠️ Video hajmi kattaligi sababli Telegram serveriga yuklashda vaqt tugadi (Timeout)."
        )
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik ({platform}): {e}")
        await status_msg.edit_text("❌ Tizimda xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring.")
    finally:
        if downloaded_file:
            await safe_remove(downloaded_file)


async def main():
    """Botni ishga tushirish funksiyasi."""
    global BOT_USERNAME
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.critical("XATOLIK: .env faylida BOT_TOKEN ko'rsatilmagan!")
        sys.exit(1)

    session = AiohttpSession(timeout=300.0)
    bot = Bot(token=BOT_TOKEN, session=session)
    try:
        me = await bot.get_me()
        if me.username:
            BOT_USERNAME = me.username
    except Exception:
        pass

    logger.info(f"Bot ishga tushirilmoqda: @{BOT_USERNAME}...")
    logger.info("Qo'llab-quvvatlanadigan platformalar: Instagram, YouTube (Sifat va MP3 tanlash bilan), Pinterest")
    if CHANNELS:
        logger.info(f"Majburiy obuna kanallari: {CHANNELS}")

    # Render Web Service uchun port ochish (Render sog'lom deb hisoblashi uchun)
    port = int(os.getenv("PORT", 0))
    if port > 0:
        try:
            from aiohttp import web
            async def health_handler(request):
                return web.Response(text="Bot is running smoothly!")
            app = web.Application()
            app.router.add_get("/", health_handler)
            app.router.add_get("/health", health_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            logger.info(f"Render Web Service porti tinglanmoqda: 0.0.0.0:{port}")
        except Exception as e:
            logger.warning(f"Web serverni ishga tushirishda ogohlantirish: {e}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
