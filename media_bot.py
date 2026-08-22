"""
Ijtimoiy tarmoqlardan (Instagram, YouTube, Facebook, X, TikTok) video va rasm
yuklab beruvchi, shuningdek musiqani nomi bo'yicha yoki audio/video fayl
orqali (Shazam kabi) topib beruvchi Telegram bot.

Katta hajmli fayllarni (2 GB gacha) yuborish uchun bu bot LOCAL Telegram Bot
API serveriga ulanadi.
"""

import os
import re
import gc
import logging
import tempfile
import asyncio
import shutil
from pathlib import Path

import requests
import yt_dlp

from telegram import (
    Update,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from telegram.request import HTTPXRequest


# ============================================================
# SOZLAMALAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
)

# Local Telegram Bot API
LOCAL_API_HOST = os.environ.get(
    "LOCAL_API_HOST",
    ""
).strip()

# Maksimal fayl hajmi
MAX_FILESIZE_MB = int(
    os.environ.get(
        "MAX_FILESIZE_MB",
        "1900"
    )
)

# AudD
AUDD_API_TOKEN = os.environ.get(
    "AUDD_API_TOKEN",
    ""
).strip()

AUDD_URL = "https://api.audd.io/"

# Admin Telegram chat ID.
# Railway Variables ichida ADMIN_CHAT_ID sifatida beriladi.
ADMIN_CHAT_ID = os.environ.get(
    "ADMIN_CHAT_ID",
    ""
).strip()

# Feedback holati:
# foydalanuvchi ID'lari feedback yozish rejimida turadi.
PENDING_FEEDBACK = set()


# ============================================================
# YOUTUBE COOKIES
# ============================================================

YOUTUBE_COOKIES_CONTENT = os.environ.get(
    "YOUTUBE_COOKIES",
    ""
).strip()

YOUTUBE_COOKIES_FILE = (
    Path(tempfile.gettempdir())
    / "youtube_cookies.txt"
)

if YOUTUBE_COOKIES_CONTENT:
    YOUTUBE_COOKIES_FILE.write_text(
        YOUTUBE_COOKIES_CONTENT,
        encoding="utf-8"
    )


# ============================================================
# DOWNLOAD PAPKA
# ============================================================

DOWNLOAD_DIR = (
    Path(tempfile.gettempdir())
    / "media_bot_downloads"
)

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# URL
# ============================================================

URL_PATTERN = re.compile(
    r"https?://\S+"
)


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


# ============================================================
# YOUTUBE
# ============================================================

YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["default"],
    }
}


# ============================================================
# DOWNLOAD SEMAPHORE
# ============================================================

# Bir vaqtning o'zida faqat bitta katta download.
# Bu Railway RAM'ini himoya qiladi.
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)


# ============================================================
# USER TANLAGAN FORMATLARNI VAQTINCHA SAQLASH
# ============================================================

# user_id -> {"url": "..."}
PENDING_YOUTUBE = {}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(
    __name__
)


# ============================================================
# SYSTEM CHECK
# ============================================================

def check_system_dependencies() -> None:

    # yt-dlp
    try:
        logger.info(
            "yt-dlp version: "
            f"{yt_dlp.version.__version__}"
        )

    except Exception:
        logger.warning(
            "yt-dlp versionini aniqlab bo'lmadi."
        )

    # Deno
    deno_path = shutil.which(
        "deno"
    )

    if deno_path:
        logger.info(
            f"Deno topildi: {deno_path}"
        )
    else:
        logger.warning(
            "Deno topilmadi!"
        )

    # FFmpeg
    ffmpeg_path = shutil.which(
        "ffmpeg"
    )

    if ffmpeg_path:
        logger.info(
            f"FFmpeg topildi: {ffmpeg_path}"
        )
    else:
        logger.warning(
            "FFmpeg topilmadi!"
        )

    # Cookies
    if (
        YOUTUBE_COOKIES_CONTENT
        and YOUTUBE_COOKIES_FILE.exists()
    ):
        logger.info(
            "YouTube cookies topildi"
        )

        logger.info(
            "Cookie file size: "
            f"{YOUTUBE_COOKIES_FILE.stat().st_size} bytes"
        )

    else:
        logger.warning(
            "YOUTUBE_COOKIES sozlanmagan."
        )


# ============================================================
# YORDAMCHI
# ============================================================

def detect_platform(url: str) -> str:

    for domain, name in PLATFORM_NAMES.items():

        if domain in url:
            return name

    return "Noma'lum manba"


def format_duration(seconds) -> str:

    if not seconds:
        return "N/A"

    seconds = int(seconds)

    h, rem = divmod(
        seconds,
        3600
    )

    m, s = divmod(
        rem,
        60
    )

    if h:
        return (
            f"{h}:"
            f"{m:02d}:"
            f"{s:02d}"
        )

    return (
        f"{m}:"
        f"{s:02d}"
    )


def build_caption(
    title: str,
    uploader: str,
    duration
) -> str:

    uploader_display = (
        uploader
        if uploader
        else "Noma'lum"
    )

    return (
        f"🎬 {title}\n"
        f"👤 {uploader_display}\n"
        f"⏱ {format_duration(duration)}"
    )


# ============================================================
# YOUTUBE FORMAT
# ============================================================

YOUTUBE_FORMATS = {

    "360": (
        "bestvideo[height<=360]"
        "+bestaudio/"
        "best[height<=360]"
    ),

    "480": (
        "bestvideo[height<=480]"
        "+bestaudio/"
        "best[height<=480]"
    ),

    "720": (
        "bestvideo[height<=720]"
        "+bestaudio/"
        "best[height<=720]"
    ),

    "1080": (
        "bestvideo[height<=1080]"
        "+bestaudio/"
        "best[height<=1080]"
    ),

    "1440": (
        "bestvideo[height<=1440]"
        "+bestaudio/"
        "best[height<=1440]"
    ),

    "2160": (
        "bestvideo[height<=2160]"
        "+bestaudio/"
        "best[height<=2160]"
    ),

    "audio": "bestaudio/best",
}

# Tugmalarda va xabarlarda ko'rsatiladigan chiroyli nomlar.
QUALITY_LABELS = {
    "360": "360p",
    "480": "480p",
    "720": "720p",
    "1080": "1080p",
    "1440": "1440p (2K)",
    "2160": "2160p (4K)",
    "audio": "🎵 MP3",
}

# 4K (2160p) video uchun qattiqroq hajm chegarasi — 1 GB.
# Oddiy MAX_FILESIZE_MB (odatda 1900 MB) juda katta 4K fayllarga yo'l
# qo'yib yuborishi mumkin, shuning uchun 4K uchun alohida, pastroq
# chegara qo'yamiz.
FOUR_K_MAX_MB = 1024


def get_max_filesize_mb(quality: str) -> int:
    """Tanlangan sifat uchun ruxsat etilgan maksimal hajmni (MB) qaytaradi."""
    if quality == "2160":
        return FOUR_K_MAX_MB
    return MAX_FILESIZE_MB


# ============================================================
# YOUTUBE OPTIONS
# ============================================================

def get_youtube_options(
    outtmpl: str,
    quality: str = "720",
    audio_only: bool = False
) -> dict:

    if audio_only:

        ydl_opts = {

            "outtmpl": outtmpl,

            "format": (
                YOUTUBE_FORMATS["audio"]
            ),

            "postprocessors": [
                {
                    "key": (
                        "FFmpegExtractAudio"
                    ),
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "restrictfilenames": True,

            "extractor_args": (
                YOUTUBE_EXTRACTOR_ARGS
            ),

            # Kichikroq buffer
            "buffersize": (
                1024 * 1024
            ),

            # 10 MB chunk
            "http_chunk_size": (
                10 * 1024 * 1024
            ),
        }

    else:

        selected_format = (
            YOUTUBE_FORMATS.get(
                quality,
                YOUTUBE_FORMATS["720"]
            )
        )

        ydl_opts = {

            "outtmpl": outtmpl,

            "format": selected_format,

            "merge_output_format": "mp4",

            "max_filesize": (
                get_max_filesize_mb(quality)
                * 1024
                * 1024
            ),

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "restrictfilenames": True,

            "extractor_args": (
                YOUTUBE_EXTRACTOR_ARGS
            ),

            # RAM'ni nazorat qilish
            "buffersize": (
                1024 * 1024
            ),

            # Chunk
            "http_chunk_size": (
                10 * 1024 * 1024
            ),

            # Merge'dan keyin
            # vaqtinchalik video qoldirmaslik
            "keepvideo": False,
        }

    # ========================================================
    # COOKIES
    # ========================================================

    if (
        YOUTUBE_COOKIES_CONTENT
        and YOUTUBE_COOKIES_FILE.exists()
    ):

        ydl_opts["cookiefile"] = (
            str(YOUTUBE_COOKIES_FILE)
        )

        logger.info(
            "yt-dlp uchun YouTube "
            "cookies ishlatilmoqda."
        )

    return ydl_opts


# ============================================================
# MEDIA DOWNLOAD
# ============================================================

def download_media(
    url: str,
    user_id: str,
    quality: str = "720"
) -> dict:

    """
    Video/rasm yuklab oladi.
    YouTube uchun quality:
        360
        480
        720
        1080
        1440 (2K)
        2160 (4K)
    """

    outtmpl = str(
        DOWNLOAD_DIR
        / f"{user_id}_%(id)s.%(ext)s"
    )

    # ========================================================
    # YOUTUBE / BOSHQA MANBA
    # ========================================================

    if (
        "youtube.com" in url
        or "youtu.be" in url
    ):

        ydl_opts = get_youtube_options(
            outtmpl=outtmpl,
            quality=quality,
            audio_only=False
        )

    else:

        # Instagram / TikTok / Facebook / X
        ydl_opts = {

            "outtmpl": outtmpl,

            "format": (
                "bestvideo+bestaudio/"
                "best"
            ),

            "merge_output_format": "mp4",

            "max_filesize": (
                MAX_FILESIZE_MB
                * 1024
                * 1024
            ),

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "restrictfilenames": True,

            "buffersize": (
                1024 * 1024
            ),

            "http_chunk_size": (
                10 * 1024 * 1024
            ),

            "keepvideo": False,
        }

    # ========================================================
    # DOWNLOAD
    # ========================================================

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        logger.info(
            f"Yuklanmoqda: {url}"
        )

        logger.info(
            f"Quality: {quality}"
        )

        info = ydl.extract_info(
            url,
            download=True
        )

        filepath = (
            ydl.prepare_filename(
                info
            )
        )

        # ====================================================
        # EXTENSION TOPISH
        # ====================================================

        if not os.path.exists(
            filepath
        ):

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

                if os.path.exists(
                    candidate
                ):

                    filepath = candidate
                    break

        if not os.path.exists(
            filepath
        ):

            raise FileNotFoundError(
                f"Yuklangan fayl topilmadi: "
                f"{filepath}"
            )

        ext = (
            os.path.splitext(
                filepath
            )[1]
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
            "duration": (
                info.get("duration")
            ),
        }


# ============================================================
# AUDIO SEARCH
# ============================================================

def download_audio_by_query(
    query: str,
    user_id: str
) -> dict:

    outtmpl = str(
        DOWNLOAD_DIR
        / f"{user_id}_search_%(id)s.%(ext)s"
    )

    ydl_opts = get_youtube_options(
        outtmpl=outtmpl,
        audio_only=True
    )

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

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

        filepath = (
            ydl.prepare_filename(
                info
            )
        )

        base = os.path.splitext(
            filepath
        )[0]

        mp3_path = (
            f"{base}.mp3"
        )

        if os.path.exists(
            mp3_path
        ):

            filepath = mp3_path

        if not os.path.exists(
            filepath
        ):

            raise FileNotFoundError(
                "MP3 fayl topilmadi."
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
            "duration": (
                info.get("duration")
            ),
        }


# ============================================================
# AUDD
# ============================================================

def recognize_song(
    filepath: str
) -> dict:

    with open(
        filepath,
        "rb"
    ) as f:

        response = requests.post(

            AUDD_URL,

            data={
                "api_token": (
                    AUDD_API_TOKEN
                ),
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

    return data.get(
        "result"
    )


# ============================================================
# ADMIN / USER INFO
# ============================================================

def get_user_info(update: Update) -> str:
    """Foydalanuvchi haqida admin uchun xavfsiz metadata."""
    user = update.effective_user

    if not user:
        return "👤 Foydalanuvchi: noma'lum"

    full_name = user.full_name or "Noma'lum"
    username = (
        f"@{user.username}"
        if user.username
        else "username yo'q"
    )

    return (
        f"👤 Ism: {full_name}\n"
        f"🔹 Username: {username}\n"
        f"🆔 User ID: {user.id}"
    )


async def send_to_admin(
    context: ContextTypes.DEFAULT_TYPE,
    text: str
) -> None:
    """Admin chatiga xabar yuboradi."""
    if not ADMIN_CHAT_ID:
        logger.warning(
            "ADMIN_CHAT_ID sozlanmagan."
        )
        return

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception(
            f"Admin'ga xabar yuborishda xato: {e}"
        )


async def notify_admin_about_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    platform: str
) -> None:
    """Har bir foydalanuvchi yuborgan linkni admin'ga yuboradi."""
    user_info = get_user_info(update)

    admin_text = (
        "🔗 YANGI LINK\n\n"
        f"{user_info}\n\n"
        f"🌐 Platforma: {platform}\n"
        f"🔗 Link:\n{url}"
    )

    await send_to_admin(
        context,
        admin_text
    )


# ============================================================
# FEEDBACK
# ============================================================

async def feedback_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Feedback rejimini boshlaydi."""
    user_id = update.effective_user.id

    PENDING_FEEDBACK.add(user_id)

    await update.message.reply_text(
        "✍️ Feedbackingizni yozib yuboring.\n\n"
        "Siz yuborgan keyingi xabar admin'ga "
        "sizning ma'lumotlaringiz bilan birga yuboriladi."
    )


async def handle_feedback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Feedbackni admin'ga yuboradi va rejimni avtomatik tugatadi."""
    user_id = update.effective_user.id

    if user_id not in PENDING_FEEDBACK:
        return

    feedback = (
        update.message.text or ""
    ).strip()

    if not feedback:
        await update.message.reply_text(
            "❌ Feedback bo'sh bo'lmasligi kerak."
        )
        return

    user_info = get_user_info(update)

    admin_text = (
        "📩 YANGI FEEDBACK\n\n"
        f"{user_info}\n\n"
        "💬 Feedback:\n"
        f"{feedback}"
    )

    await send_to_admin(
        context,
        admin_text
    )

    # Feedback yuborilgandan keyin avtomatik tugaydi.
    PENDING_FEEDBACK.discard(user_id)

    await update.message.reply_text(
        "✅ Feedbackingiz admin'ga yuborildi. Rahmat!"
    )


# ============================================================
# QUALITY MENU
# ============================================================

def youtube_quality_keyboard() -> InlineKeyboardMarkup:

    keyboard = [

        [
            InlineKeyboardButton(
                "360p",
                callback_data="ytq:360"
            ),
            InlineKeyboardButton(
                "480p",
                callback_data="ytq:480"
            ),
        ],

        [
            InlineKeyboardButton(
                "720p",
                callback_data="ytq:720"
            ),
            InlineKeyboardButton(
                "1080p",
                callback_data="ytq:1080"
            ),
        ],

        [
            InlineKeyboardButton(
                "2K (1440p)",
                callback_data="ytq:1440"
            ),
            InlineKeyboardButton(
                "4K (2160p)",
                callback_data="ytq:2160"
            ),
        ],

        [
            InlineKeyboardButton(
                "🎵 MP3",
                callback_data="ytq:audio"
            ),
        ],
    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# DOWNLOAD AND SEND
# ============================================================

async def download_and_send(
    message,
    status_msg,
    url: str,
    user_id: str,
    quality: str
) -> None:

    result = None

    # ========================================================
    # FAQAT BITTA KATTA DOWNLOAD
    # ========================================================

    async with DOWNLOAD_SEMAPHORE:

        await status_msg.edit_text(
            "⏳ Yuklanmoqda...\n"
            f"🎚 Sifat: "
            f"{QUALITY_LABELS.get(quality, quality)}"
        )

        try:

            result = await asyncio.to_thread(

                download_media,

                url,

                user_id,

                quality
            )

        except yt_dlp.utils.DownloadError as e:

            error_text = str(e)

            logger.error(
                "yt-dlp xatosi: "
                f"{error_text[:1000]}"
            )

            error_lower = (
                error_text.lower()
            )

            if (
                "sign in" in error_lower
                or "confirm" in error_lower
            ):

                await status_msg.edit_text(
                    "❌ YouTube server "
                    "so'rovni bot deb aniqladi.\n\n"
                    "Cookies va JS runtime "
                    "sozlamalarini tekshiring."
                )

            elif (
                "max-filesize"
                in error_lower
                or "file is larger"
                in error_lower
            ):

                if quality == "2160":

                    await status_msg.edit_text(
                        "❌ 4K (2160p) video hajmi "
                        f"{FOUR_K_MAX_MB} MB (1 GB) dan katta "
                        "bo'lgani uchun yubora olmayman.\n\n"
                        "Boshqa sifatni tanlab ko'ring "
                        "(masalan 1080p yoki 2K)."
                    )

                else:

                    await status_msg.edit_text(
                        f"❌ Fayl "
                        f"{get_max_filesize_mb(quality)} MB dan katta."
                    )

            elif "ffmpeg" in error_lower:

                await status_msg.edit_text(
                    "❌ FFmpeg bilan "
                    "bog'liq xato."
                )

            else:

                await status_msg.edit_text(
                    "❌ Yuklab olishda xato."
                )

            return

        except Exception as e:

            logger.exception(
                "Kutilmagan download xatosi"
            )

            await status_msg.edit_text(
                "❌ Kutilmagan xato yuz berdi."
            )

            return

    # ========================================================
    # FILE YUBORISH
    # ========================================================

    if result is None:
        return

    filepath = result["path"]

    try:

        file_size_mb = (
            os.path.getsize(
                filepath
            )
            / (1024 * 1024)
        )

        size_limit_mb = get_max_filesize_mb(quality)

        if (
            file_size_mb
            > size_limit_mb
        ):

            await status_msg.edit_text(
                f"❌ Fayl juda katta: "
                f"{file_size_mb:.0f} MB "
                f"(limit: {size_limit_mb} MB)"
            )

            return

        caption = build_caption(

            result["title"],

            result.get(
                "uploader"
            ),

            result.get(
                "duration"
            )
        )

        duration = result.get(
            "duration"
        )

        await status_msg.edit_text(
            f"📤 Yuborilmoqda...\n"
            f"📦 Hajmi: "
            f"{file_size_mb:.1f} MB"
        )

        # ====================================================
        # MUHIM:
        # Faylni RAM'ga to'liq o'qimaymiz
        # ====================================================

        with open(
            filepath,
            "rb"
        ) as f:

            input_file = InputFile(

                f,

                filename=os.path.basename(
                    filepath
                ),

                read_file_handle=False,
            )

            if result["type"] == "photo":

                await message.reply_chat_action(
                    "upload_photo"
                )

                await message.reply_photo(

                    photo=input_file,

                    caption=caption
                )

            elif result["type"] == "audio":

                await message.reply_chat_action(
                    "upload_voice"
                )

                await message.reply_audio(

                    audio=input_file,

                    caption=caption,

                    title=(
                        result["title"][:64]
                    ),

                    performer=(
                        result.get(
                            "uploader"
                        )
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

                    video=input_file,

                    caption=caption,

                    supports_streaming=True,

                    write_timeout=1800,

                    read_timeout=1800,
                )

        await status_msg.delete()

    except Exception as e:

        logger.exception(
            f"Telegram upload xatosi: {e}"
        )

        try:

            await status_msg.edit_text(
                "❌ Faylni Telegram'ga "
                "yuborishda xato."
            )

        except Exception:
            pass

    finally:

        # ====================================================
        # FILE DELETE
        # ====================================================

        try:

            os.remove(
                filepath
            )

        except OSError:
            pass

        # ====================================================
        # PYTHON MEMORY CLEANUP
        # ====================================================

        gc.collect()

        logger.info(
            "Download fayli o'chirildi "
            "va garbage collector ishlatildi."
        )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    text = (

        "Salom! 👋 Men video/musiqa "
        "yuklab beruvchi botman.\n\n"

        "📎 Instagram, YouTube, Facebook, "
        "X yoki TikTok havolasini yuboring.\n\n"

        "🎵 Qo'shiq nomini yozing — "
        "YouTube'dan audio topib beraman.\n\n"

        "🎧 Audio/video/voice yuboring — "
        "musiqani aniqlashga harakat qilaman.\n\n"

        "⚠️ Faqat foydalanishga haqqingiz "
        "bo'lgan kontentdan foydalaning."
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


# ============================================================
# LINK
# ============================================================

async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    text = (
        update.message.text
        or ""
    )

    match = URL_PATTERN.search(
        text
    )

    if not match:
        return

    url = match.group(0)

    user_id = str(
        update.effective_user.id
    )

    platform = detect_platform(
        url
    )

    # Har bir linkni admin'ga yuborish.
    await notify_admin_about_link(
        update,
        context,
        url,
        platform
    )

    # ========================================================
    # YOUTUBE: SIFAT TANLASH
    # ========================================================

    if platform == "YouTube":

        PENDING_YOUTUBE[
            user_id
        ] = {
            "url": url
        }

        await update.message.reply_text(
            "🎬 YouTube video topildi.\n\n"
            "📊 Qaysi sifatda yuklaymiz?\n"
            f"ℹ️ 4K (2160p) uchun hajm chegarasi: {FOUR_K_MAX_MB} MB (1 GB)",
            reply_markup=(
                youtube_quality_keyboard()
            )
        )

        return

    # ========================================================
    # BOSHQA PLATFORMALAR
    # ========================================================

    status_msg = (
        await update.message.reply_text(
            f"⏳ {platform}'dan yuklab olinmoqda..."
        )
    )

    await update.message.reply_chat_action(
        "upload_video"
    )

    try:

        async with DOWNLOAD_SEMAPHORE:
            result = await asyncio.to_thread(
                download_media,
                url,
                user_id,
                "720"
            )

    except Exception as e:

        logger.exception(
            f"{platform} xatosi: {e}"
        )

        await status_msg.edit_text(
            "❌ Yuklab olishda xato."
        )

        return

    await download_and_send_existing(
        update.message,
        status_msg,
        result
    )


# ============================================================
# EXISTING RESULT SEND
# ============================================================

async def download_and_send_existing(
    message,
    status_msg,
    result: dict
) -> None:

    filepath = result["path"]

    try:

        file_size_mb = (
            os.path.getsize(
                filepath
            )
            / (1024 * 1024)
        )

        if (
            file_size_mb
            > MAX_FILESIZE_MB
        ):

            await status_msg.edit_text(
                f"❌ Fayl juda katta: "
                f"{file_size_mb:.0f} MB"
            )

            return

        caption = build_caption(

            result["title"],

            result.get(
                "uploader"
            ),

            result.get(
                "duration"
            )
        )

        duration = result.get(
            "duration"
        )

        await status_msg.edit_text(
            f"📤 Yuborilmoqda... "
            f"({file_size_mb:.1f} MB)"
        )

        with open(
            filepath,
            "rb"
        ) as f:

            input_file = InputFile(

                f,

                filename=os.path.basename(
                    filepath
                ),

                read_file_handle=False,
            )

            if result["type"] == "photo":

                await message.reply_chat_action(
                    "upload_photo"
                )

                await message.reply_photo(
                    photo=input_file,
                    caption=caption
                )

            elif result["type"] == "audio":

                await message.reply_chat_action(
                    "upload_voice"
                )

                await message.reply_audio(

                    audio=input_file,

                    caption=caption,

                    title=(
                        result["title"][:64]
                    ),

                    performer=(
                        result.get(
                            "uploader"
                        )
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

                    video=input_file,

                    caption=caption,

                    supports_streaming=True,

                    write_timeout=1800,

                    read_timeout=1800,
                )

        await status_msg.delete()

    finally:

        try:
            os.remove(
                filepath
            )
        except OSError:
            pass

        gc.collect()


# ============================================================
# YOUTUBE QUALITY CALLBACK
# ============================================================

async def youtube_quality_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    query = update.callback_query

    await query.answer()

    user_id = str(
        query.from_user.id
    )

    pending = PENDING_YOUTUBE.get(
        user_id
    )

    if not pending:

        await query.edit_message_text(
            "❌ Bu tanlov eskirib qolgan. "
            "YouTube linkni qayta yuboring."
        )

        return

    url = pending["url"]

    # Keyin qayta ishlatilmasin
    PENDING_YOUTUBE.pop(
        user_id,
        None
    )

    quality = (
        query.data.split(
            ":",
            1
        )[1]
    )

    quality_text = QUALITY_LABELS.get(
        quality,
        f"{quality}p"
    )

    await query.edit_message_text(
        f"⏳ Tanlandi: "
        f"{quality_text}\n\n"
        "Yuklash boshlanmoqda..."
    )

    status_msg = (
        query.message
    )

    # callback message bilan ishlash
    # uchun original messagega javob yuborish
    fake_message = (
        query.message
    )

    await download_and_send(
        fake_message,
        status_msg,
        url,
        user_id,
        quality
    )


# ============================================================
# TEXT SEARCH
# ============================================================

async def handle_text_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    query_text = (
        update.message.text
        or ""
    ).strip()

    if not query_text:
        return

    user_id = str(
        update.effective_user.id
    )

    status_msg = (
        await update.message.reply_text(
            f"🔍 Qidirilmoqda: "
            f"{query_text}"
        )
    )

    await update.message.reply_chat_action(
        "upload_voice"
    )

    async with DOWNLOAD_SEMAPHORE:

        try:

            result = (
                await asyncio.to_thread(
                    download_audio_by_query,
                    query_text,
                    user_id
                )
            )

        except yt_dlp.utils.DownloadError as e:

            logger.error(
                f"Qidiruv xatosi: "
                f"{str(e)[:1000]}"
            )

            await status_msg.edit_text(
                "❌ Topilmadi yoki "
                "yuklab bo'lmadi."
            )

            return

        except Exception as e:

            logger.exception(
                f"Search xatosi: {e}"
            )

            await status_msg.edit_text(
                "❌ Kutilmagan xato."
            )

            return

    await download_and_send_existing(
        update.message,
        status_msg,
        result
    )


# ============================================================
# TEXT
# ============================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    user_id = update.effective_user.id

    # Feedback rejimi link/search logikasidan oldin tekshiriladi.
    if user_id in PENDING_FEEDBACK:
        await handle_feedback(
            update,
            context
        )
        return

    text = (
        update.message.text
        or ""
    )

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


# ============================================================
# MEDIA RECOGNITION
# ============================================================

async def handle_media_recognition(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:

    if not AUDD_API_TOKEN:

        await update.message.reply_text(
            "🎧 AUDD_API_TOKEN sozlanmagan."
        )

        return

    message = update.message

    tg_file = None

    if message.voice:

        tg_file = (
            await message.voice.get_file()
        )

    elif message.audio:

        tg_file = (
            await message.audio.get_file()
        )

    elif message.video_note:

        tg_file = (
            await message.video_note.get_file()
        )

    elif message.video:

        tg_file = (
            await message.video.get_file()
        )

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

        tg_file = (
            await message.document.get_file()
        )

    if tg_file is None:
        return

    user_id = str(
        update.effective_user.id
    )

    status_msg = (
        await message.reply_text(
            "🎧 Musiqa aniqlanmoqda..."
        )
    )

    local_path = (
        DOWNLOAD_DIR
        / (
            f"recognize_"
            f"{user_id}_"
            f"{tg_file.file_unique_id}"
        )
    )

    await tg_file.download_to_drive(
        custom_path=str(local_path)
    )

    try:

        result = (
            await asyncio.to_thread(
                recognize_song,
                str(local_path)
            )
        )

    except Exception as e:

        logger.exception(
            f"AudD xatosi: {e}"
        )

        await status_msg.edit_text(
            "❌ Musiqani aniqlashda xato."
        )

        return

    finally:

        try:
            os.remove(
                local_path
            )
        except OSError:
            pass

        gc.collect()

    if not result:

        await status_msg.edit_text(
            "😕 Musiqa aniqlanmadi."
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

    search_query = (
        f"{artist} - {title}"
        .strip(" -")
        or title
        or artist
    )

    await status_msg.edit_text(
        f"🎵 Topildi: "
        f"{artist} — {title}\n"
        f"⏳ MP3 yuklanmoqda..."
    )

    await message.reply_chat_action(
        "upload_voice"
    )

    async with DOWNLOAD_SEMAPHORE:

        try:

            download_result = (
                await asyncio.to_thread(
                    download_audio_by_query,
                    search_query,
                    user_id
                )
            )

        except Exception as e:

            logger.exception(
                f"Audio yuklash xatosi: {e}"
            )

            await status_msg.edit_text(
                f"🎵 Aniqlangan: "
                f"{artist} — {title}\n"
                "❌ Audio yuklab bo'lmadi."
            )

            return

    await download_and_send_existing(
        message,
        status_msg,
        download_result
    )


# ============================================================
# BUILD APPLICATION
# ============================================================

def build_application() -> Application:

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN "
            "topilmadi."
        )

    check_system_dependencies()

    request = HTTPXRequest(

        connect_timeout=60,

        read_timeout=1800,

        write_timeout=1800,

        pool_timeout=60,
    )

    builder = (
        Application.builder()
        .token(
            TELEGRAM_BOT_TOKEN
        )
        .request(
            request
        )
    )

    # ========================================================
    # LOCAL API
    # ========================================================

    if LOCAL_API_HOST:

        base_url = (
            f"http://"
            f"{LOCAL_API_HOST}"
            f"/bot"
        )

        base_file_url = (
            f"http://"
            f"{LOCAL_API_HOST}"
            f"/file/bot"
        )

        builder = (
            builder
            .base_url(
                base_url
            )
            .base_file_url(
                base_file_url
            )
        )

        logger.info(
            "Local Bot API server: "
            f"{LOCAL_API_HOST}"
        )

    else:

        logger.warning(
            "LOCAL_API_HOST "
            "sozlanmagan."
        )

    # ========================================================
    # AUDD
    # ========================================================

    if not AUDD_API_TOKEN:

        logger.warning(
            "AUDD_API_TOKEN "
            "sozlanmagan."
        )

    if ADMIN_CHAT_ID:
        logger.info(
            "ADMIN_CHAT_ID sozlangan."
        )
    else:
        logger.warning(
            "ADMIN_CHAT_ID sozlanmagan — "
            "link va feedback admin'ga yuborilmaydi."
        )

    # ========================================================
    # COOKIES
    # ========================================================

    if (
        YOUTUBE_COOKIES_CONTENT
        and YOUTUBE_COOKIES_FILE.exists()
    ):

        logger.info(
            "YouTube cookies topildi"
        )

    else:

        logger.warning(
            "YOUTUBE_COOKIES "
            "sozlanmagan."
        )

    return builder.build()


# ============================================================
# MAIN
# ============================================================

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

    # Feedback
    app.add_handler(
        CommandHandler(
            "feedback",
            feedback_command
        )
    )

    # YouTube quality buttons
    app.add_handler(
        CallbackQueryHandler(
            youtube_quality_callback,
            pattern=r"^ytq:"
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


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
