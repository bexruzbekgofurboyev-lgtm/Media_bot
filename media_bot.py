"""
Ijtimoiy tarmoqlardan (Instagram, YouTube, Facebook, X, TikTok) video va rasm
yuklab beruvchi, shuningdek musiqani nomi bo'yicha yoki audio/video fayl
orqali (Shazam kabi) topib beruvchi Telegram bot.

Katta hajmli fayllarni (2 GB gacha) yuborish uchun bu bot LOCAL Telegram Bot
API serveriga ulanadi (oddiy api.telegram.org emas). Local server sozlash
yo'riqnomasi DEPLOY.md faylida.
"""

import os
import re
import logging
import tempfile
import asyncio
import shutil
from pathlib import Path

import requests
import yt_dlp

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest


# ==================== SOZLAMALAR ====================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Local Bot API server manzili
LOCAL_API_HOST = os.environ.get("LOCAL_API_HOST", "").strip()

# Maksimal fayl hajmi
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "1900"))

# AudD API
AUDD_API_TOKEN = os.environ.get("AUDD_API_TOKEN", "").strip()
AUDD_URL = "https://api.audd.io/"


# ==================== YOUTUBE COOKIES ====================

YOUTUBE_COOKIES_CONTENT = os.environ.get("YOUTUBE_COOKIES", "").strip()

YOUTUBE_COOKIES_FILE = (
    Path(tempfile.gettempdir()) / "youtube_cookies.txt"
)

if YOUTUBE_COOKIES_CONTENT:
    YOUTUBE_COOKIES_FILE.write_text(
        YOUTUBE_COOKIES_CONTENT,
        encoding="utf-8"
    )


# ==================== PAPKA ====================

DOWNLOAD_DIR = (
    Path(tempfile.gettempdir()) / "media_bot_downloads"
)

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ==================== URL ====================

URL_PATTERN = re.compile(r"https?://\S+")


PLATFORM_NAMES = {
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "tiktok.com": "TikTok",
}


# ==================== YOUTUBE ====================

# YouTube clientlarini ortiqcha majburlamaslik
YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["default"],
    }
}


# ==================== LOGGING ====================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==================== SYSTEM CHECK ====================

def check_system_dependencies() -> None:
    """
    Railway serverda kerakli dependencylar mavjudligini tekshiradi.
    """

    # yt-dlp versiyasi
    try:
        logger.info(
            f"yt-dlp version: {yt_dlp.version.__version__}"
        )
    except Exception:
        logger.warning(
            "yt-dlp versionini aniqlab bo'lmadi."
        )

    # Deno
    deno_path = shutil.which("deno")

    if deno_path:
        logger.info(
            f"Deno topildi: {deno_path}"
        )
    else:
        logger.warning(
            "Deno topilmadi! "
            "YouTube uchun zamonaviy yt-dlp EJS ishlashi "
            "uchun Deno kerak bo'lishi mumkin."
        )

    # FFmpeg
    ffmpeg_path = shutil.which("ffmpeg")

    if ffmpeg_path:
        logger.info(
            f"FFmpeg topildi: {ffmpeg_path}"
        )
    else:
        logger.warning(
            "FFmpeg topilmadi! "
            "Video/audio birlashtirish va MP3 konvertatsiya "
            "uchun FFmpeg kerak bo'lishi mumkin."
        )

    # Cookies
    if YOUTUBE_COOKIES_CONTENT:
        logger.info(
            f"YouTube cookies mavjud: {YOUTUBE_COOKIES_FILE}"
        )
    else:
        logger.warning(
            "YOUTUBE_COOKIES sozlanmagan. "
            "YouTube ayrim Railway IP manzillarini "
            "bot sifatida bloklashi mumkin."
        )


# ==================== YORDAMCHI ====================

def detect_platform(url: str) -> str:
    for domain, name in PLATFORM_NAMES.items():
        if domain in url:
            return name

    return "Noma'lum manba"


def format_duration(seconds) -> str:

    if not seconds:
        return "N/A"

    seconds = int(seconds)

    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def build_caption(
    title: str,
    uploader: str,
    duration
) -> str:

    uploader_display = (
        uploader if uploader else "Noma'lum"
    )

    return (
        f"🎬 {title}\n"
        f"👤 {uploader_display}\n"
        f"⏱ {format_duration(duration)}"
    )


# ==================== YOUTUBE OPTIONS ====================

def get_youtube_options(
    outtmpl: str,
    audio_only: bool = False
) -> dict:

    if audio_only:

        ydl_opts = {
            "outtmpl": outtmpl,

            # Audio
            "format": "ba/best",

            # MP3
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],

            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,

            "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        }

    else:

        ydl_opts = {
            "outtmpl": outtmpl,

            # Video + Audio
            "format": "bestvideo+bestaudio/best",

            # MP4 ga birlashtirish
            "merge_output_format": "mp4",

            "max_filesize": (
                MAX_FILESIZE_MB * 1024 * 1024
            ),

            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,

            "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        }

    # ====================
    # COOKIES
    # ====================

    if (
        YOUTUBE_COOKIES_CONTENT
        and YOUTUBE_COOKIES_FILE.exists()
    ):
        ydl_opts["cookiefile"] = (
            str(YOUTUBE_COOKIES_FILE)
        )

        logger.info(
            "yt-dlp uchun YouTube cookies ishlatilmoqda."
        )

    return ydl_opts


# ==================== VIDEO / MEDIA DOWNLOAD ====================

def download_media(
    url: str,
    user_id: str
) -> dict:

    """
    Havoladan video/rasm yuklab oladi.
    """

    outtmpl = str(
        DOWNLOAD_DIR /
        f"{user_id}_%(id)s.%(ext)s"
    )

    ydl_opts = get_youtube_options(
        outtmpl=outtmpl,
        audio_only=False
    )

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:

        logger.info(
            f"Yuklanmoqda: {url}"
        )

        info = ydl.extract_info(
            url,
            download=True
        )

        filepath = ydl.prepare_filename(info)

        # Ba'zi formatlarda extension o'zgarishi mumkin
        if not os.path.exists(filepath):

            base = os.path.splitext(
                filepath
            )[0]

            for ext in (
                "mp4",
                "mkv",
                "webm",
                "jpg",
                "jpeg",
                "png",
                "webp"
            ):

                candidate = (
                    f"{base}.{ext}"
                )

                if os.path.exists(candidate):
                    filepath = candidate
                    break

        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"Yuklangan fayl topilmadi: {filepath}"
            )

        ext = (
            os.path.splitext(filepath)[1]
            .lower()
        )

        media_type = (
            "photo"
            if ext in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp"
            )
            else "video"
        )

        return {
            "path": filepath,
            "type": media_type,
            "title": (
                info.get("title")
                or "Media"
            ),
            "uploader": (
                info.get("uploader")
                or "Noma'lum"
            ),
            "duration": info.get("duration"),
        }


# ==================== AUDIO SEARCH ====================

def download_audio_by_query(
    query: str,
    user_id: str
) -> dict:

    """
    YouTube'dan nomi bo'yicha musiqa qidirib,
    MP3 qilib yuklab oladi.
    """

    outtmpl = str(
        DOWNLOAD_DIR /
        f"{user_id}_search_%(id)s.%(ext)s"
    )

    ydl_opts = get_youtube_options(
        outtmpl=outtmpl,
        audio_only=True
    )

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:

        logger.info(
            f"YouTube search: {query}"
        )

        info = ydl.extract_info(
            f"ytsearch1:{query}",
            download=True
        )

        if (
            "entries" in info
            and info["entries"]
        ):
            info = info["entries"][0]

        filepath = ydl.prepare_filename(info)

        base = os.path.splitext(
            filepath
        )[0]

        mp3_path = f"{base}.mp3"

        if os.path.exists(mp3_path):
            filepath = mp3_path

        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"MP3 fayl topilmadi: {filepath}"
            )

        return {
            "path": filepath,
            "type": "audio",
            "title": (
                info.get("title")
                or "Musiqa"
            ),
            "uploader": (
                info.get("uploader")
                or "Noma'lum"
            ),
            "duration": info.get("duration"),
        }


# ==================== AUDD ====================

def recognize_song(
    filepath: str
) -> dict:

    """
    AudD.io orqali fayldagi musiqani aniqlaydi.
    """

    with open(filepath, "rb") as f:

        response = requests.post(
            AUDD_URL,

            data={
                "api_token": AUDD_API_TOKEN,
                "return": (
                    "apple_music,spotify"
                ),
            },

            files={
                "file": f
            },

            timeout=60,
        )

    response.raise_for_status()

    data = response.json()

    return data.get("result")


# ==================== NATIJANI YUBORISH ====================

async def send_download_result(
    message,
    status_msg,
    result: dict
) -> None:

    filepath = result["path"]

    try:

        file_size_mb = (
            os.path.getsize(filepath)
            / (1024 * 1024)
        )

        if file_size_mb > MAX_FILESIZE_MB:

            await status_msg.edit_text(
                f"❌ Fayl juda katta "
                f"({file_size_mb:.0f} MB), "
                f"yubora olmayman."
            )

            return

        caption = build_caption(
            result["title"],
            result.get("uploader"),
            result.get("duration")
        )

        duration = result.get(
            "duration"
        )

        await status_msg.edit_text(
            f"📤 Yuborilmoqda... "
            f"({file_size_mb:.1f} MB)"
        )

        with open(filepath, "rb") as f:

            if result["type"] == "photo":

                await message.reply_chat_action(
                    "upload_photo"
                )

                await message.reply_photo(
                    photo=f,
                    caption=caption
                )

            elif result["type"] == "audio":

                await message.reply_chat_action(
                    "upload_voice"
                )

                await message.reply_audio(
                    audio=f,
                    caption=caption,
                    title=result["title"][:64],
                    performer=(
                        result.get("uploader")
                        or ""
                    )[:64],
                    duration=(
                        int(duration)
                        if duration
                        else None
                    ),
                    write_timeout=1800,
                    read_timeout=1800,
                )

            else:

                await message.reply_chat_action(
                    "upload_video"
                )

                await message.reply_video(
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    write_timeout=1800,
                    read_timeout=1800,
                )

        await status_msg.delete()

    finally:

        try:
            os.remove(filepath)

        except OSError:
            pass


# ==================== START ====================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    text = (
        "Salom! 👋 Men video/musiqa "
        "yuklab beruvchi va topib beruvchi botman.\n\n"

        "📎 Instagram, YouTube, Facebook, "
        "X yoki TikTok havolasini yuboring — "
        "video/rasmni yuklab beraman.\n\n"

        "🎵 Qo'shiq nomini yozing "
        "(masalan \"Dildora Sevgi\") — "
        "men uni topib, audio qilib yuboraman.\n\n"

        "🎧 Audio, video yoki ovozli xabar yuboring — "
        "undagi musiqani tanib, sifatli faylini "
        "topib beraman.\n\n"

        "⚠️ Eslatma: faqat mualliflik huquqi "
        "ruxsat bergan yoki shaxsiy foydalanish "
        "uchun kontentni yuklab oling."
    )

    await update.message.reply_text(
        text
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    await start(
        update,
        context
    )


# ==================== LINK ====================

async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    text = update.message.text or ""

    match = URL_PATTERN.search(text)

    if not match:
        return

    url = match.group(0)

    user_id = str(
        update.effective_user.id
    )

    platform = detect_platform(url)

    status_msg = await update.message.reply_text(
        f"⏳ {platform}'dan yuklab olinmoqda..."
    )

    await update.message.reply_chat_action(
        "upload_video"
    )

    try:

        result = await asyncio.to_thread(
            download_media,
            url,
            user_id
        )

    except yt_dlp.utils.DownloadError as e:

        error_text = str(e)

        error_lower = (
            error_text.lower()
        )

        if (
            "max-filesize" in error_lower
            or "file is larger" in error_lower
        ):

            await status_msg.edit_text(
                f"❌ Fayl "
                f"{MAX_FILESIZE_MB} MB dan katta, "
                f"yubora olmayman."
            )

        elif (
            "sign in" in error_lower
            or "confirm you're not a bot"
            in error_lower
            or "confirm" in error_lower
        ):

            await status_msg.edit_text(
                "❌ YouTube server so'rovni "
                "avtomatik bot deb aniqladi.\n\n"

                "Railway'dagi YouTube cookies "
                "va JS runtime sozlamalarini "
                "tekshirish kerak."
            )

        elif "ffmpeg" in error_lower:

            await status_msg.edit_text(
                "❌ FFmpeg topilmadi. "
                "Railway serverda FFmpeg "
                "o'rnatilishi kerak."
            )

        else:

            await status_msg.edit_text(
                "❌ Yuklab olishda xato.\n\n"
                "Havola to'g'riligini yoki "
                "kontent ochiq ekanligini "
                "tekshiring."
            )

        logger.error(
            f"yt-dlp xatosi: "
            f"{error_text[:1000]}"
        )

        return

    except Exception as e:

        logger.exception(
            "Kutilmagan xato"
        )

        await status_msg.edit_text(
            "❌ Kutilmagan xato yuz berdi. "
            "Qayta urinib ko'ring."
        )

        return

    await send_download_result(
        update.message,
        status_msg,
        result
    )


# ==================== TEXT SEARCH ====================

async def handle_text_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    query = (
        update.message.text or ""
    ).strip()

    if not query:
        return

    user_id = str(
        update.effective_user.id
    )

    status_msg = await update.message.reply_text(
        f"🔍 Qidirilmoqda: {query}"
    )

    await update.message.reply_chat_action(
        "upload_voice"
    )

    try:

        result = await asyncio.to_thread(
            download_audio_by_query,
            query,
            user_id
        )

    except yt_dlp.utils.DownloadError as e:

        logger.error(
            f"Qidiruv xatosi: "
            f"{str(e)[:1000]}"
        )

        await status_msg.edit_text(
            "❌ Topilmadi yoki yuklab bo'lmadi.\n\n"
            "YouTube bot blokirovkasi yoki "
            "JS runtime muammosi bo'lishi mumkin."
        )

        return

    except Exception as e:

        logger.exception(
            "Kutilmagan search xatosi"
        )

        await status_msg.edit_text(
            "❌ Kutilmagan xato yuz berdi. "
            "Qayta urinib ko'ring."
        )

        return

    await send_download_result(
        update.message,
        status_msg,
        result
    )


# ==================== TEXT ====================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    text = update.message.text or ""

    if URL_PATTERN.search(text):

        await handle_link(
            update,
            context
        )

    else:

        await handle_text_search(
            update,
            context
        )


# ==================== MEDIA RECOGNITION ====================

async def handle_media_recognition(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    if not AUDD_API_TOKEN:

        await update.message.reply_text(
            "🎧 Musiqani fayl orqali tanish "
            "funksiyasi hali sozlanmagan "
            "(AUDD_API_TOKEN yo'q)."
        )

        return

    message = update.message

    tg_file = None

    if message.voice:

        tg_file = await message.voice.get_file()

    elif message.audio:

        tg_file = await message.audio.get_file()

    elif message.video_note:

        tg_file = await message.video_note.get_file()

    elif message.video:

        tg_file = await message.video.get_file()

    elif (
        message.document
        and message.document.mime_type
        and (
            message.document.mime_type.startswith(
                "audio/"
            )
            or
            message.document.mime_type.startswith(
                "video/"
            )
        )
    ):

        tg_file = await message.document.get_file()

    if tg_file is None:
        return

    user_id = str(
        update.effective_user.id
    )

    status_msg = await message.reply_text(
        "🎧 Musiqa aniqlanmoqda..."
    )

    local_path = (
        DOWNLOAD_DIR
        / f"recognize_"
          f"{user_id}_"
          f"{tg_file.file_unique_id}"
    )

    await tg_file.download_to_drive(
        custom_path=str(local_path)
    )

    try:

        result = await asyncio.to_thread(
            recognize_song,
            str(local_path)
        )

    except Exception as e:

        logger.exception(
            f"AudD xatosi: {e}"
        )

        await status_msg.edit_text(
            "❌ Aniqlashda xato yuz berdi. "
            "Qayta urinib ko'ring."
        )

        return

    finally:

        try:
            os.remove(local_path)

        except OSError:
            pass

    if not result:

        await status_msg.edit_text(
            "😕 Kechirasiz, bu musiqani "
            "aniqlay olmadim."
        )

        return

    artist = result.get(
        "artist",
        ""
    )

    title = result.get(
        "title",
        ""
    )

    query = (
        f"{artist} - {title}"
        .strip(" -")
        or title
        or artist
    )

    await status_msg.edit_text(
        f"🎵 Topildi: "
        f"{artist} — {title}\n"
        f"⏳ Yuklab olinmoqda..."
    )

    await message.reply_chat_action(
        "upload_voice"
    )

    try:

        download_result = (
            await asyncio.to_thread(
                download_audio_by_query,
                query,
                user_id
            )
        )

    except Exception as e:

        logger.exception(
            f"Audio yuklashda xato: {e}"
        )

        await status_msg.edit_text(
            f"🎵 Aniqlandi: "
            f"{artist} — {title}\n"
            f"Lekin audio faylni "
            f"yuklab bo'lmadi."
        )

        return

    await send_download_result(
        message,
        status_msg,
        download_result
    )


# ==================== APPLICATION ====================

def build_application() -> Application:

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN "
            "muhit o'zgaruvchisi topilmadi."
        )

    # Dependency check
    check_system_dependencies()

    request = HTTPXRequest(

        connect_timeout=60,

        read_timeout=1800,

        write_timeout=1800,

        pool_timeout=60,
    )

    builder = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
    )

    # ====================
    # LOCAL TELEGRAM API
    # ====================

    if LOCAL_API_HOST:

        base_url = (
            f"http://{LOCAL_API_HOST}/bot"
        )

        base_file_url = (
            f"http://{LOCAL_API_HOST}/file/bot"
        )

        builder = (
            builder
            .base_url(base_url)
            .base_file_url(base_file_url)
        )

        logger.info(
            "Local Bot API server ishlatilmoqda: "
            f"{LOCAL_API_HOST}"
        )

    else:

        logger.warning(
            "LOCAL_API_HOST sozlanmagan — "
            "oddiy api.telegram.org ishlatiladi. "
            "Bu holatda Telegram fayl limiti "
            "alohida cheklov bo'lishi mumkin."
        )

    # ====================
    # AUDD
    # ====================

    if not AUDD_API_TOKEN:

        logger.warning(
            "AUDD_API_TOKEN sozlanmagan — "
            "musiqa tanish funksiyasi o'chirilgan."
        )

    # ====================
    # YOUTUBE COOKIES
    # ====================

    if YOUTUBE_COOKIES_CONTENT:

        logger.info(
            "YouTube cookies topildi."
        )

    else:

        logger.warning(
            "YOUTUBE_COOKIES sozlanmagan."
        )

    return builder.build()


# ==================== MAIN ====================

def main() -> None:

    app = build_application()

    # Commands
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    # Text
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_text
        )
    )

    # Media
    app.add_handler(
        MessageHandler(
            filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE
            | filters.Document.AUDIO
            | filters.Document.VIDEO,
            handle_media_recognition
        )
    )

    logger.info(
        "Media bot ishga tushmoqda..."
    )

    app.run_polling()


# ==================== START ====================

if __name__ == "__main__":
    main()
