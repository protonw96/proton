import os
import glob
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import FSInputFile
from aiogram.filters import Command
import yt_dlp

TOKEN = '8718214616:AAHbsSxCn866X6Mp20dpLR__cciaW93aEPE'

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Настройки для обхода 403 и блокировок YouTube
YOUTUBE_CLIENT_ARGS = {
    'youtube': {
        # Принудительно заставляем использовать клиенты android и ios/mweb
        'player_client': ['android', 'ios', 'mweb'],
        'player_skip': ['webpage', 'configs'],
    }
}

def get_playlist_info(url):
    """
    Быстро извлекает список ссылок из плейлиста или одиночного видео.
    """
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'extractor_args': YOUTUBE_CLIENT_ARGS,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception:
            return [], None
        
        if not info:
            return [], None

        if 'entries' in info:
            playlist_title = info.get('title', 'Плейлист')
            urls = []
            for entry in info['entries']:
                if entry:
                    if entry.get('url'):
                        urls.append(entry['url'])
                    elif entry.get('id'):
                        urls.append(f"https://www.youtube.com/watch?v={entry['id']}")
            return urls, playlist_title
        else:
            return [url], None

def download_audio(url, msg_id):
    """
    Скачивает видео, конвертирует в MP3 и вшивает обложку.
    """
    out_template = f"%(id)s_{msg_id}.%(ext)s"
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            },
            {
                'key': 'EmbedThumbnail',
            }
        ],
        'writethumbnail': True,
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'geo_bypass': True,
        # Обход 403 через Android/iOS API клиентов YouTube:
        'extractor_args': YOUTUBE_CLIENT_ARGS,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            return info
        except Exception as err:
            print(f"Ошибка загрузки ({url}): {err}")
            return None


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь ссылку на видео или плейлист (YouTube, TikTok, Shorts и др.), "
        "и я пришлю MP3 с метаданными и обложкой."
    )

@dp.message(F.text.startswith("http"))
async def handle_url(message: types.Message):
    url = message.text.strip()
    status_msg = await message.answer("🔍 Обрабатываю ссылку...")

    try:
        loop = asyncio.get_running_loop()
        urls, playlist_title = await loop.run_in_executor(None, get_playlist_info, url)
        
        if not urls:
            await status_msg.edit_text("❌ Не удалось найти аудио. Возможно, ссылка недоступна или приватна.")
            return

        total = len(urls)
        limit = 50
        if total > limit:
            await status_msg.edit_text(f"⚠️ В плейлисте {total} треков. Скачиваю первые {limit}.")
            urls = urls[:limit]
            total = limit

        if total > 1:
            await status_msg.edit_text(f"🗂 Плейлист: <b>{playlist_title}</b>\nЗагрузка {total} треков...", parse_mode="HTML")
        else:
            await status_msg.edit_text("⏳ Скачиваю и конвертирую...")

        success_count = 0
        
        for i, video_url in enumerate(urls, 1):
            try:
                if total > 1 and (i == 1 or i % 3 == 0):
                    await status_msg.edit_text(f"⏳ Скачиваю {i} из {total}...")

                info = await loop.run_in_executor(None, download_audio, video_url, message.message_id)
                
                if not info:
                    continue

                video_id = info.get('id')
                title = info.get('title', 'Аудиозапись')
                author = info.get('uploader', info.get('creator', 'Неизвестный автор'))
                
                mp3_path = f"{video_id}_{message.message_id}.mp3"
                
                if os.path.exists(mp3_path):
                    audio = FSInputFile(mp3_path)
                    await message.answer_audio(
                        audio=audio,
                        caption=f"🎵 <b>{title}</b>\n👤 {author}",
                        title=title,
                        performer=author,
                        parse_mode="HTML"
                    )
                    success_count += 1
                    
                    # Очистка всех созданных временных файлов
                    for file in glob.glob(f"{video_id}_{message.message_id}.*"):
                        try:
                            os.remove(file)
                        except OSError:
                            pass
                            
                if total > 1:
                    await asyncio.sleep(2.0)
                
            except Exception as item_err:
                print(f"Ошибка при обработке элемента {video_url}: {item_err}")
                continue

        if total > 1:
            await status_msg.edit_text(f"✅ Готово! Отправлено: {success_count} из {total}.")
        else:
            if success_count > 0:
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Не удалось загрузить данный файл.")

    except Exception as general_err:
        await status_msg.edit_text(f"❌ Ошибка:\n\n`{str(general_err)}`", parse_mode="Markdown")

async def main():
    print("🤖 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

