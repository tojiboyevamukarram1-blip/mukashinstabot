import asyncio
import glob
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple

import httpx
import yt_dlp
from config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Telegram Bot API maksimal fayl hajmi: 50 MB
TELEGRAM_MAX_BYTES = 49.5 * 1024 * 1024

# Aria2c mavjudligini aniqlash
ARIA2_PATH = shutil.which('aria2c')
HAS_ARIA2 = bool(ARIA2_PATH)
if HAS_ARIA2:
    logger.info(f"Maksimal tezlik uchun Aria2c topildi: {ARIA2_PATH}")
else:
    logger.info("Aria2c topilmadi, standart tezkor oqim ishlatiladi.")

# ===================== URL REGEX PATTERNLARI =====================

INSTAGRAM_REGEX = re.compile(
    r'(https?://(?:www\.)?(?:instagram\.com|instagr\.am)/(?:p|reel|reels|tv|share)/[a-zA-Z0-9_\-]+/?(?:\?[^\s]*)?)',
    re.IGNORECASE
)

YOUTUBE_REGEX = re.compile(
    r'(https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=[a-zA-Z0-9_\-]+|shorts/[a-zA-Z0-9_\-]+|embed/[a-zA-Z0-9_\-]+|live/[a-zA-Z0-9_\-]+)|youtu\.be/[a-zA-Z0-9_\-]+)(?:[^\s]*))',
    re.IGNORECASE
)

PINTEREST_REGEX = re.compile(
    r'(https?://(?:www\.)?(?:pinterest\.com|pin\.it|pinterest\.[a-z]{2,3})/(?:pin/[0-9]+|[^\s]+)/?(?:\?[^\s]*)?)',
    re.IGNORECASE
)

HTTP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# Doimiy HTTP/2 mijoz ulanishi (Pinterest uchun ulanish vaqtini 0.2s gacha kamaytiradi)
try:
    FAST_HTTPX_CLIENT = httpx.Client(
        headers=HTTP_HEADERS,
        follow_redirects=True,
        http2=True,
        timeout=10.0,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
    )
except Exception:
    FAST_HTTPX_CLIENT = httpx.Client(
        headers=HTTP_HEADERS,
        follow_redirects=True,
        timeout=10.0
    )


# ===================== PLATFORM ANIQLASH =====================

def detect_platform(text: str) -> Optional[Tuple[str, str]]:
    """Matn ichidan platformani aniqlash."""
    if not text:
        return None

    match = INSTAGRAM_REGEX.search(text)
    if match:
        url = match.group(1).split('?')[0].rstrip('/')
        return ("instagram", url)

    match = YOUTUBE_REGEX.search(text)
    if match:
        url = match.group(1)
        return ("youtube", url)

    match = PINTEREST_REGEX.search(text)
    if match:
        url = match.group(1).split('?')[0].rstrip('/')
        return ("pinterest", url)

    return None


def _get_speed_opts() -> dict:
    """Maksimal yuklash tezligi parametrlarini qaytaradi."""
    opts = {
        'concurrent_fragment_downloads': 16,
        'buffersize': 2097152,  # 2MB xotira buferi
        'nocheckcertificate': True,
        'http_headers': HTTP_HEADERS,
    }
    if HAS_ARIA2:
        opts['external_downloader'] = {'default': 'aria2c'}
        opts['external_downloader_args'] = {
            'aria2c': [
                '-x', '16',               # 16 ta parallel ulanish
                '-s', '16',               # 16 ta bo'lak
                '-j', '16',
                '-k', '1M',
                '--min-split-size=1M',
                '--file-allocation=none',
                '--summary-interval=0',
                '--console-log-level=warn',
            ]
        }
    return opts


# ===================== 1. INSTAGRAM TEZKOR YUKLASH =====================

def _sync_download_instagram(url: str, file_prefix: str) -> Tuple[Optional[str], Optional[dict]]:
    """
    Instagram Reels va videolarni eng yuqori barqarorlik va tezlikda yuklab oladi.
    Instagram CDN bloklashini oldini olish uchun toza oqimdan foydalaniladi.
    """
    output_template = str(DOWNLOADS_DIR / f"{file_prefix}.%(ext)s")

    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'playlist_items': '1',
        'nocheckcertificate': True,
        'concurrent_fragment_downloads': 4,
        'buffersize': 1048576,
        'socket_timeout': 45,
        'retries': 10,
        'fragment_retries': 10,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]

        pattern = str(DOWNLOADS_DIR / f"{file_prefix}.*")
        found_files = glob.glob(pattern)

        if found_files:
            return found_files[0], info

        return None, None
    except Exception as e:
        raise e


# ===================== 2. YOUTUBE MA'LUMOT VA FORMATLAR =====================

def _sync_get_youtube_info(url: str) -> dict:
    """YouTube video ma'lumotlarini soniya ulushlarida tezkor oladi."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios'],
                'player_skip': ['webpage', 'configs', 'js'],
            }
        },
        'http_headers': HTTP_HEADERS,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


async def get_youtube_info(url: str) -> dict:
    """Asinxron tarzda YouTube ma'lumotini olish."""
    return await asyncio.to_thread(_sync_get_youtube_info, url)


def _sync_download_youtube_quality(url: str, file_prefix: str, quality: str = "720") -> Tuple[Optional[str], Optional[dict]]:
    """Tanlangan sifat bo'yicha YouTube videosini ko'p oqimli maksimal tezlikda yuklaydi."""
    output_template = str(DOWNLOADS_DIR / f"{file_prefix}.%(ext)s")
    q = quality if quality in ["720", "480", "360"] else "720"

    ydl_opts = {
        'format': (
            f'bestvideo[height<={q}][filesize<42M]+bestaudio[filesize<5M]/'
            f'best[height<={q}][filesize<48M]/'
            f'bestvideo[height<={q}][filesize_approx<42M]+bestaudio[filesize_approx<5M]/'
            f'best[height<={q}][filesize_approx<48M]/'
            f'bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]/'
            f'best[height<={q}][ext=mp4]/'
            f'bestvideo[height<={q}]+bestaudio/'
            f'best[height<={q}]/'
            'best'
        ),
        'merge_output_format': 'mp4',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'playlist_items': '1',
        'nocheckcertificate': True,
        'concurrent_fragment_downloads': 4,
        'buffersize': 1048576,
        'socket_timeout': 60,
        'retries': 15,
        'fragment_retries': 15,
        'file_access_retries': 5,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios'],
                'player_skip': ['webpage', 'configs', 'js'],
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]

        pattern = str(DOWNLOADS_DIR / f"{file_prefix}.*")
        found_files = glob.glob(pattern)

        if not found_files:
            return None, None

        downloaded_path = found_files[0]

        if os.path.getsize(downloaded_path) > TELEGRAM_MAX_BYTES:
            compressed_path = _compress_video(downloaded_path, file_prefix)
            if compressed_path and os.path.exists(compressed_path):
                try:
                    os.remove(downloaded_path)
                except OSError:
                    pass
                downloaded_path = compressed_path

        return downloaded_path, info
    except Exception as e:
        raise e


async def download_youtube_video(url: str, quality: str = "720") -> Tuple[Optional[str], Optional[dict]]:
    """YouTube videoni tanlangan sifatda asinxron yuklab olish."""
    file_prefix = f"yt_{quality}_{uuid.uuid4().hex[:8]}"
    try:
        return await asyncio.to_thread(_sync_download_youtube_quality, url, file_prefix, quality)
    except Exception as e:
        pattern = str(DOWNLOADS_DIR / f"{file_prefix}.*")
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass
        raise e


def _compress_audio(input_path: str, file_prefix: str, duration: float) -> Optional[str]:
    """Audio faylni Telegram'ning 50 MB limitiga sig'adigan qilib tezkor siqadi."""
    output_path = str(DOWNLOADS_DIR / f"{file_prefix}_comp.mp3")
    try:
        if duration <= 0:
            duration = 1800.0

        target_kbps = int((45 * 1024 * 1024 * 8) / duration / 1000)
        if target_kbps < 32:
            target_kbps = 32
        if target_kbps > 160:
            target_kbps = 160

        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-c:a', 'libmp3lame',
            '-b:a', f"{target_kbps}k",
            output_path
        ]
        logger.info(f"Audio siqilmoqda: {os.path.getsize(input_path)/(1024*1024):.1f} MB -> bitrate={target_kbps}k")
        subprocess.run(cmd, capture_output=True, timeout=90)

        if os.path.exists(output_path) and os.path.getsize(output_path) <= TELEGRAM_MAX_BYTES:
            try:
                os.remove(input_path)
            except OSError:
                pass
            return output_path
        elif os.path.exists(output_path):
            os.remove(output_path)
    except Exception as e:
        logger.error(f"Audio siqishda xatolik: {e}")
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
    return input_path


def _sync_download_youtube_audio(url: str, file_prefix: str) -> Tuple[Optional[str], Optional[dict]]:
    """YouTube videodan faqat audioni (MP3 formatida) ultra-tezkor ajratib oladi."""
    output_template = str(DOWNLOADS_DIR / f"{file_prefix}.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'playlist_items': '1',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'concurrent_fragment_downloads': 4,
        'buffersize': 1048576,
        'socket_timeout': 60,
        'retries': 15,
        'fragment_retries': 15,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios'],
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]

        mp3_pattern = str(DOWNLOADS_DIR / f"{file_prefix}.mp3")
        found_files = glob.glob(mp3_pattern)

        if not found_files:
            found_files = glob.glob(str(DOWNLOADS_DIR / f"{file_prefix}.*"))

        if not found_files:
            return None, None

        audio_path = found_files[0]

        if os.path.getsize(audio_path) > TELEGRAM_MAX_BYTES:
            duration = info.get('duration', 1800.0) if info else 1800.0
            audio_path = _compress_audio(audio_path, file_prefix, float(duration))

        return audio_path, info

    except Exception as e:
        raise e


async def download_youtube_audio(url: str) -> Tuple[Optional[str], Optional[dict]]:
    """YouTube'dan MP3 audio formatda asinxron yuklab olish."""
    file_prefix = f"yt_mp3_{uuid.uuid4().hex[:8]}"
    try:
        return await asyncio.to_thread(_sync_download_youtube_audio, url, file_prefix)
    except Exception as e:
        pattern = str(DOWNLOADS_DIR / f"{file_prefix}.*")
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass
        raise e


def _compress_video(input_path: str, file_prefix: str) -> Optional[str]:
    """FFmpeg ultrafast rejimi orqali videoni Telegram 50 MB limitiga aniq sig'diradi."""
    output_path = str(DOWNLOADS_DIR / f"{file_prefix}_compressed.mp4")

    try:
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            input_path
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 60.0

        if duration <= 0:
            duration = 60.0

        if duration > 3600:
            logger.warning(f"Video juda uzun ({duration/60:.1f} daqiqa).")
            return None

        target_size_bits = 45 * 1024 * 1024 * 8
        total_bitrate = int(target_size_bits / duration)

        if duration > 1200:
            audio_k = "48k"
            audio_bitrate = 48 * 1000
        elif duration > 600:
            audio_k = "64k"
            audio_bitrate = 64 * 1000
        else:
            audio_k = "96k"
            audio_bitrate = 96 * 1000

        video_bitrate = total_bitrate - audio_bitrate

        if video_bitrate < 200_000:
            scale_filter = 'scale=-2:min(240\\,ih)'
        elif video_bitrate < 400_000:
            scale_filter = 'scale=-2:min(360\\,ih)'
        elif video_bitrate < 800_000:
            scale_filter = 'scale=-2:min(480\\,ih)'
        else:
            scale_filter = 'scale=-2:min(720\\,ih)'

        if video_bitrate < 60_000:
            video_bitrate = 60_000

        compress_cmd = [
            'ffmpeg', '-y',
            '-threads', '0',
            '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-tune', 'fastdecode',
            '-b:v', str(video_bitrate),
            '-maxrate', str(int(video_bitrate * 1.1)),
            '-bufsize', str(video_bitrate * 2),
            '-vf', scale_filter,
            '-c:a', 'aac',
            '-b:a', audio_k,
            '-fs', '48M',
            '-movflags', '+faststart',
            output_path
        ]

        logger.info(f"Ultra-tezkor siqish: {os.path.getsize(input_path)/(1024*1024):.1f} MB -> <=48 MB")
        subprocess.run(compress_cmd, capture_output=True, timeout=240)

        if os.path.exists(output_path) and os.path.getsize(output_path) <= TELEGRAM_MAX_BYTES and os.path.getsize(output_path) > 1000:
            return output_path
        elif os.path.exists(output_path):
            os.remove(output_path)

    except Exception as e:
        logger.error(f"Video siqishda xatolik: {e}")
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

    return None


# ===================== 3. PINTEREST ULTRA-TEZKOR YUKLASH =====================

def _sync_download_pinterest(url: str, file_prefix: str) -> Tuple[Optional[str], Optional[dict]]:
    """Pinterest rasmlarini doimiy HTTP/2 ulanish orqali 0.5 soniyada yuklab oladi."""
    try:
        response = FAST_HTTPX_CLIENT.get(url)
        response.raise_for_status()
        html = response.text

        has_video = (
            '<meta property="og:video"' in html
            or 'name="og:video"' in html
            or '"video_list"' in html
            or 'v3.pinimg.com' in html
        )

        if not has_video:
            return _extract_and_download_pinterest_image(FAST_HTTPX_CLIENT, html, file_prefix)

    except Exception as e:
        logger.info(f"Pinterest tahlilda ogohlantirish: {e}")

    # Video pinlar uchun ko'p oqimli yuklash
    output_template = str(DOWNLOADS_DIR / f"{file_prefix}.%(ext)s")
    ydl_opts = {
        'format': 'best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'playlist_items': '1',
    }
    ydl_opts.update(_get_speed_opts())

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]

        pattern = str(DOWNLOADS_DIR / f"{file_prefix}.*")
        found_files = glob.glob(pattern)
        if found_files:
            return found_files[0], info
    except Exception as e:
        logger.info(f"Pinterest yt-dlp xatoligi: {e}")

    return _download_pinterest_image_fallback(url, file_prefix)


def _extract_and_download_pinterest_image(client: httpx.Client, html: str, file_prefix: str) -> Tuple[Optional[str], Optional[dict]]:
    """Pinterest sahifasidan original rasmni ajratib olib soniya ichida yuklaydi."""
    image_url = None
    info = {}

    og_match = re.search(r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\'](https?://[^"\']+)["\']', html)
    if not og_match:
        og_match = re.search(r'content=["\'](https?://[^"\']+)["\']\s+(?:property|name)=["\']og:image["\']', html)

    if og_match:
        image_url = og_match.group(1)

    if not image_url:
        img_match = re.search(r'<img[^>]+src=["\'](https?://i\.pinimg\.com/[^"\']+)["\']', html)
        if img_match:
            image_url = img_match.group(1)

    if not image_url:
        pin_match = re.search(r'(https?://i\.pinimg\.com/(?:originals|736x|564x|474x|236x)/[a-zA-Z0-9/_.\-]+)', html)
        if pin_match:
            image_url = pin_match.group(1)

    if not image_url:
        raise Exception("Pinterest sahifasida rasm topilmadi")

    original_url = re.sub(r'/(236x|474x|564x|736x)/', '/originals/', image_url)

    title_match = re.search(r'<meta\s+(?:property|name)=["\']og:title["\']\s+content=["\']([^"\']*)["\']', html)
    if title_match:
        info['title'] = title_match.group(1)

    info['direct_url'] = original_url

    try:
        img_res = client.get(original_url, timeout=10.0)
        img_res.raise_for_status()
        final_bytes = img_res.content
        final_url = original_url
    except Exception:
        img_res = client.get(image_url, timeout=10.0)
        img_res.raise_for_status()
        final_bytes = img_res.content
        final_url = image_url

    content_type = img_res.headers.get('content-type', '').lower()
    if 'png' in content_type:
        ext = 'png'
    elif 'gif' in content_type:
        ext = 'gif'
    elif 'webp' in content_type:
        ext = 'webp'
    else:
        ext = 'jpg'

    file_path = str(DOWNLOADS_DIR / f"{file_prefix}.{ext}")
    with open(file_path, 'wb') as f:
        f.write(final_bytes)

    info['url'] = final_url
    info['direct_url'] = final_url
    return file_path, info


def _download_pinterest_image_fallback(url: str, file_prefix: str) -> Tuple[Optional[str], Optional[dict]]:
    """Zaxira Pinterest yuklash."""
    res = FAST_HTTPX_CLIENT.get(url)
    res.raise_for_status()
    return _extract_and_download_pinterest_image(FAST_HTTPX_CLIENT, res.text, file_prefix)


# ===================== ASINXRON INSTAGRAM & PINTEREST DISPATCHER =====================

async def download_media(platform: str, url: str) -> Tuple[Optional[str], Optional[dict]]:
    """Instagram va Pinterest uchun ultra-tezkor ko'p oqimli asinxron yuklovchi."""
    file_prefix = f"{platform[:2]}_{uuid.uuid4().hex[:10]}"
    try:
        if platform == "instagram":
            file_path, info = await asyncio.to_thread(_sync_download_instagram, url, file_prefix)
        elif platform == "pinterest":
            file_path, info = await asyncio.to_thread(_sync_download_pinterest, url, file_prefix)
        else:
            file_path, info = None, None

        return file_path, info
    except Exception as e:
        pattern = str(DOWNLOADS_DIR / f"{file_prefix}.*")
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass
        raise e
