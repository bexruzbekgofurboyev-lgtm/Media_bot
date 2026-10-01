"""
Media Downloader Bot (Cobalt API v10 + Fallback Zaxira Tizimi)
"""

import os
import re
import gc
import logging
import tempfile
import asyncio
import subprocess
from pathlib import Path
from shazamio import Shazam
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yt_dlp

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

# ============================================================
# SOZLAMALAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
LOCAL_API_HOST = os.environ.get("LOCAL_API_HOST", "").strip()
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "1900"))
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "media_bot_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)
PENDING_YOUTUBE = {}
PENDING_FEEDBACK = set()

# Asosiy mahalliy server va Zaxira ochiq serverlar
COBALT_API_URLS = [
    "http://127.0.0.1:9000",                 # Asosiy (Instagram uchun zo'r)
    "https://api.cobalt.tools",              # Zaxira 1
    "https://cobalt-api.kwiatekit.com",      # Zaxira 2
    "https://cobalt.zorner.me",              # Zaxira 3
    "https://co.wuk.sh"                      # Zaxira 4
]

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

QUALITY_LABELS = {
    "360": "360p",
    "480": "480p",
    "720": "720p",
    "1080": "1080p",
    "1440": "1440p (2K)",
    "2160": "2160p (4K)",
    "audio": "🎵 MP3",
}

URL_PATTERN = re.compile(r"https?://\S+")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# So'rovlar barqarorligi uchun session
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries))
session.mount('https://', HTTPAdapter(max_retries=retries))

def get_max_filesize_mb(quality: str) -> int:
    return 1900 if quality == "2160" else MAX_FILESIZE_MB

def build_caption(title: str = "Media") -> str:
    return f"🎬 {title}\n🤖 Media Bot"

# ============================================================
# COBALT API ORQALI YUKLASH (Zaxira Serverlar bilan)
# ============================================================

def download_via_cobalt(url: str, user_id: str, quality: str = "720", audio_only: bool = False) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    clean_url = url.split("?")[0] if "youtube.com/shorts/" in url else url
    payload = {"url": clean_url}

    if audio_only or quality == "audio":
        payload["downloadMode"] = "audio"
    else:
        if quality == "2160": vQuality = "2160"
        elif quality in ["1440", "1080", "720", "480", "360"]: vQuality = quality
        else: vQuality = "720"
        payload["videoQuality"] = vQuality

    api_response = None
    last_error = ""

    # Barcha serverlarni birma-bir tekshiramiz
    for api_url in COBALT_API_URLS:
        try:
            base_url = api_url.rstrip("/")
            logger.info(f"Cobalt so'rovi yuborilmoqda: {base_url}")
            
            r = session.post(f"{base_url}/", headers=headers, json=payload, timeout=25)
            
            if r.status_code in [200, 202]:
                data = r.json()
                if data.get("status") in ["stream", "redirect", "success", "picker"]:
                    api_response = data
                    logger.info(f"Muvaffaqiyatli server: {base_url}")
                    break
            else:
                last_error = r.text
                logger.warning(f"Server xato qaytardi ({base_url}): {last_error}")
        except Exception as e:
            logger.warning(f"Serverga ulanib bo'lmadi ({api_url}): {e}")
            continue

    if not api_response:
        if "youtube.login" in last_error or "login" in last_error:
            raise ValueError("Barcha serverlar band yoki YouTube bu videoni cheklab qo'ygan. Birozdan so'ng qayta urinib ko'ring.")
        raise ValueError(f"Yuklash imkonsiz bo'ldi. So'nggi xato: {last_error[:100]}")

    download_link = api_response.get("url")
    if not download_link and api_response.get("status") == "picker":
        picker_items = api_response.get("picker")
        if picker_items and isinstance(picker_items, list):
            download_link = picker_items[0].get("url")

    if not download_link:
        raise ValueError("Cobalt fayl havolasini qaytarmadi.")

    title = api_response.get("filename", "Media")
    filepath = str(DOWNLOAD_DIR / f"{user_id}_cobalt_{title}")
    
    logger.info(f"Fayl tortilmoqda: {download_link[:50]}...")
    
    dl_req = session.get(download_link, stream=True, timeout=60)
    dl_req.raise_for_status()

    content_type = dl_req.headers.get("content-type", "")
    if "audio" in content_type or audio_only or quality == "audio":
        media_type = "audio"
        if not filepath.endswith(".mp3"): filepath += ".mp3"
    elif "image" in content_type:
        media_type = "photo"
        if not filepath.endswith(".jpg"): filepath += ".jpg"
    else:
        media_type = "video"
        if not filepath.endswith(".mp4"): filepath += ".mp4"

    with open(filepath, 'wb') as f:
        for chunk in dl_req.iter_content(chunk_size=8192):
            if chunk: f.write(chunk)

    return {
        "path": filepath,
        "type": media_type,
        "title": title,
    }

# ============================================================
# QIDIRUV (yt-dlp bilan, sababi u DNS qotmaydi)
# ============================================================

def download_audio_by_query(query: str, user_id: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "extract_flat": True, 
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}}
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        logger.info(f"YouTube qidiruv: {query}")
        try:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if not info or not info.get("entries"):
                raise Exception("Qidiruv natijasi bo'sh.")
            
            video_url = info["entries"][0].get("url")
            if not video_url:
                raise Exception("Video havolasi topilmadi.")
                
            return download_via_cobalt(video_url, user_id, audio_only=True)
            
        except Exception as e:
            raise Exception(f"Audio topishda xato: {str(e)}")

# ============================================================
# SHAZAM RECOGNITION
# ============================================================

def extract_recognition_clip(input_path: str) -> str:
    output_path = f"{input_path}_clip.mp3"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", input_path, "-t", "25", "-vn", "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2", output_path], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        if os.path.exists(output_path): return output_path
    except Exception: pass
    return input_path

async def recognize_song(filepath: str) -> dict | None:
    shazam = Shazam()
    out = await shazam.recognize(filepath)
    track = out.get("track")
    if not track: return None
    return {"title": track.get("title", ""), "artist": track.get("subtitle", "")}

# ============================================================
# INLINE KEYBOARDS
# ============================================================

def youtube_quality_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("360p", callback_data="ytq:360"), InlineKeyboardButton("480p", callback_data="ytq:480")],
        [InlineKeyboardButton("720p", callback_data="ytq:720"), InlineKeyboardButton("1080p", callback_data="ytq:1080")],
        [InlineKeyboardButton("2K (1440p)", callback_data="ytq:1440"), InlineKeyboardButton("4K (2160p)", callback_data="ytq:2160")],
        [InlineKeyboardButton("🎵 MP3", callback_data="ytq:audio")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ============================================================
# SENDER (Fayllarni yuborish)
# ============================================================

async def download_and_send(message, status_msg, url: str, user_id: str, quality: str) -> None:
    result = None
    async with DOWNLOAD_SEMAPHORE:
        await status_msg.edit_text(f"⏳ Yuklanmoqda... Sifat: {QUALITY_LABELS.get(quality, quality)}")
        try:
            result = await asyncio.to_thread(download_via_cobalt, url, user_id, quality)
        except Exception as e:
            logger.error(f"Xato ushlandi: {str(e)}")
            await status_msg.edit_text(f"❌ Yuklab olishda xato yuz berdi:\n\n{str(e)[:150]}")
            return

    if result is None: return
    await process_and_send_file(message, status_msg, result, quality)

async def download_and_send_existing(message, status_msg, result: dict) -> None:
    await process_and_send_file(message, status_msg, result, "720")

async def process_and_send_file(message, status_msg, result: dict, quality: str):
    filepath = result["path"]
    try:
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        limit = get_max_filesize_mb(quality)

        if file_size_mb > limit:
            await status_msg.edit_text(f"❌ Fayl juda katta: {file_size_mb:.0f} MB (Limit: {limit} MB)")
            return

        caption = build_caption(result["title"])
        await status_msg.edit_text(f"📤 Yuborilmoqda... ({file_size_mb:.1f} MB)")

        with open(filepath, "rb") as f:
            input_file = InputFile(f, filename=os.path.basename(filepath), read_file_handle=False)
            
            if result["type"] == "photo":
                await message.reply_chat_action("upload_photo")
                await message.reply_photo(photo=input_file, caption=caption)
            elif result["type"] == "audio":
                await message.reply_chat_action("upload_voice")
                await message.reply_audio(audio=input_file, caption=caption, title=result["title"][:64], write_timeout=1800, read_timeout=1800)
            else:
                await message.reply_chat_action("upload_video")
                await message.reply_video(video=input_file, caption=caption, supports_streaming=True, write_timeout=1800, read_timeout=1800)
        await status_msg.delete()
    except Exception as e:
        logger.exception(f"Telegram upload xatosi: {e}")
        try: await status_msg.edit_text("❌ Yuborishda xatolik yuz berdi.")
        except: pass
    finally:
        try: os.remove(filepath)
        except: pass
        gc.collect()

# ============================================================
# HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Salom! 👋 Men video/musiqa yuklab beruvchi botman.\n\n"
        "📎 Instagram, YouTube, X yoki TikTok havolasini yuboring.\n"
        "🎵 Qo'shiq nomini yozing — YouTube'dan topib beraman.\n"
        "🎧 Audio/video/voice yuboring — Shazam kabi aniqlayman."
    )
    await update.message.reply_text(text)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = URL_PATTERN.search(update.message.text or "").group(0)
    user_id = str(update.effective_user.id)
    
    platform = "Media"
    for domain, name in PLATFORM_NAMES.items():
        if domain in url:
            platform = name
            break

    if platform == "YouTube":
        PENDING_YOUTUBE[user_id] = {"url": url}
        await update.message.reply_text(
            "🎬 YouTube video topildi.\n📊 Qaysi sifatda yuklaymiz?",
            reply_markup=youtube_quality_keyboard()
        )
        return

    status_msg = await update.message.reply_text(f"⏳ {platform}'dan yuklab olinmoqda...")
    await update.message.reply_chat_action("upload_video")

    try:
        async with DOWNLOAD_SEMAPHORE:
            result = await asyncio.to_thread(download_via_cobalt, url, user_id, "720")
    except Exception as e:
        logger.exception(f"{platform} yuklash xatosi")
        await status_msg.edit_text(f"❌ Yuklashda xato:\n{str(e)[:150]}")
        return

    await download_and_send_existing(update.message, status_msg, result)

async def youtube_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    if user_id not in PENDING_YOUTUBE:
        await query.edit_message_text("❌ Tanlov eskirgan. Linkni qayta yuboring.")
        return

    url = PENDING_YOUTUBE.pop(user_id)["url"]
    quality = query.data.split(":", 1)[1]
    
    await query.edit_message_text(f"⏳ {QUALITY_LABELS.get(quality, quality)} yuklanmoqda...")
    await download_and_send(query.message, query.message, url, user_id, quality)

async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_text = (update.message.text or "").strip()
    if not query_text: return

    user_id = str(update.effective_user.id)
    status_msg = await update.message.reply_text(f"🔍 Qidirilmoqda: {query_text}")
    await update.message.reply_chat_action("upload_voice")

    async with DOWNLOAD_SEMAPHORE:
        try:
            result = await asyncio.to_thread(download_audio_by_query, query_text, user_id)
        except Exception as e:
            logger.error(f"Qidiruv xatosi: {e}")
            await status_msg.edit_text(f"❌ Topilmadi:\n{str(e)[:150]}")
            return

    await download_and_send_existing(update.message, status_msg, result)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if URL_PATTERN.search(text):
        await handle_link(update, context)
    else:
        await handle_text_search(update, context)

# ============================================================
# CLOUD DOWNLOADER (Media aniqlash)
# ============================================================

def cloud_download_file(file_id: str, dest_path: str) -> None:
    resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"): raise RuntimeError("Telegram getFile xatosi")

    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{data['result']['file_path']}"
    file_resp = requests.get(file_url, timeout=120)
    file_resp.raise_for_status()

    with open(dest_path, "wb") as f:
        f.write(file_resp.content)

async def handle_media_recognition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    file_id = msg.voice.file_id if msg.voice else msg.audio.file_id if msg.audio else msg.video_note.file_id if msg.video_note else msg.video.file_id if msg.video else (msg.document.file_id if msg.document and msg.document.mime_type and msg.document.mime_type.startswith(('audio/', 'video/')) else None)
    
    if not file_id: return

    user_id = str(update.effective_user.id)
    status_msg = await msg.reply_text("🎧 Musiqa aniqlanmoqda...")
    local_path = DOWNLOAD_DIR / f"rec_{user_id}_{file_id}.mp4"

    try:
        await asyncio.to_thread(cloud_download_file, file_id, str(local_path))
        clip_path = await asyncio.to_thread(extract_recognition_clip, str(local_path))
        result = await recognize_song(clip_path)
    except Exception as e:
        logger.exception("Shazam xatosi")
        await status_msg.edit_text("❌ Musiqani aniqlashda xato.")
        return
    finally:
        for p in {str(local_path), f"{local_path}_clip.mp3"}:
            try: os.remove(p)
            except: pass
        gc.collect()

    if not result:
        await status_msg.edit_text("😕 Musiqa aniqlanmadi.")
        return

    search_query = f"{result['artist']} - {result['title']}".strip(" -")
    await status_msg.edit_text(f"🎵 Topildi: {search_query}\n⏳ MP3 yuklanmoqda...")
    await msg.reply_chat_action("upload_voice")

    async with DOWNLOAD_SEMAPHORE:
        try:
            dl_res = await asyncio.to_thread(download_audio_by_query, search_query, user_id)
        except Exception:
            await status_msg.edit_text(f"🎵 Topildi: {search_query}\n❌ Lekin audioni yuklab bo'lmadi.")
            return

    await download_and_send_existing(msg, status_msg, dl_res)

# ============================================================
# MAIN / BUILDER
# ============================================================

def build_application() -> Application:
    request = HTTPXRequest(connect_timeout=60, read_timeout=1800, write_timeout=1800, pool_timeout=60)
    builder = Application.builder().token(TELEGRAM_BOT_TOKEN).request(request)

    if LOCAL_API_HOST:
        builder = builder.base_url(f"http://{LOCAL_API_HOST}/bot").base_file_url(f"http://{LOCAL_API_HOST}/file/bot")
        logger.info(f"Local Bot API server: {LOCAL_API_HOST}")

    return builder.build()

def main() -> None:
    app = build_application()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CallbackQueryHandler(youtube_quality_callback, pattern=r"^ytq:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE | filters.Document.AUDIO | filters.Document.VIDEO, handle_media_recognition))

    logger.info("Bot ishga tushmoqda (Zaxira tizimli Cobalt API bilan)...")
    app.run_polling()

if __name__ == "__main__":
    main()
