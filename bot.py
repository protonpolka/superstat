"""
Telegram Bot for Brawl Stars Player Statistics
Fetches player stats image from brawlbot.xyz (same style as sltbot.com)
and posts it to a Telegram channel.
"""

import os
import logging
import re
import urllib.parse

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BRAWL_STARS_API_KEY = os.getenv("BRAWL_STARS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

if not TELEGRAM_TOKEN or not BRAWL_STARS_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN and BRAWL_STARS_API_KEY must be set in .env")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Bot / Dispatcher ─────────────────────────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ── API bases ─────────────────────────────────────────────────────────────────
BS_API_BASE = "https://bsproxy.royaleapi.dev/v1"
BRAWLBOT_IMAGE_BASE = "https://brawlbot.xyz/api/image/rank"


async def fetch_player(tag: str) -> dict:
    """Fetch player data from the Brawl Stars API."""
    encoded_tag = urllib.parse.quote(tag)
    url = f"{BS_API_BASE}/players/{encoded_tag}"
    headers = {"Authorization": f"Bearer {BRAWL_STARS_API_KEY}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            logger.info(f"BS API: {url} -> {resp.status}")
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 404:
                raise ValueError("Игрок не найден. Проверьте тег.")
            elif resp.status == 403:
                text = await resp.text()
                logger.error(f"403: {text}")
                raise PermissionError("Ошибка авторизации API.")
            else:
                text = await resp.text()
                logger.error(f"API {resp.status}: {text}")
                raise ConnectionError(f"Ошибка API ({resp.status})")


async def fetch_stats_image(tag: str) -> bytes | None:
    """Fetch the ready-made stats image from brawlbot.xyz (sltbot-style)."""
    clean_tag = tag.lstrip("#")
    url = f"{BRAWLBOT_IMAGE_BASE}/{clean_tag}"
    logger.info(f"Fetching image: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            logger.info(f"Image: {url} -> {resp.status}, type={resp.headers.get('Content-Type','?')}")
            if resp.status == 200:
                data = await resp.read()
                ct = resp.headers.get("Content-Type", "")
                if "image" in ct or data[:4] == b'\x89PNG' or data[:2] == b'\xff\xd8':
                    logger.info(f"Image OK: {len(data)} bytes")
                    return data
                logger.warning(f"Not an image: {ct}")
            else:
                logger.warning(f"Image fetch failed: {resp.status}")
    return None


def generate_fallback_image(data: dict) -> bytes:
    """Simple fallback card if brawlbot.xyz is unavailable."""
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    W, H = 800, 400
    img = Image.new("RGB", (W, H), (30, 30, 46))
    draw = ImageDraw.Draw(img)

    def font(size, bold=False):
        p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold \
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    name = data.get("name", "Unknown")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    highest = data.get("highestTrophies", 0)

    draw.rectangle([(0, 0), (W, 5)], fill=(250, 200, 60))
    draw.text((40, 25), name, fill="white", font=font(36, True))
    draw.text((40, 70), tag, fill=(160, 160, 180), font=font(18))
    y = 110
    for line in [
        f"Trophies: {trophies:,} / {highest:,}",
        f"3v3: {data.get('3vs3Victories',0):,}  Solo: {data.get('soloVictories',0):,}  Duo: {data.get('duoVictories',0):,}",
        f"Brawlers: {len(data.get('brawlers',[]))}",
    ]:
        draw.text((40, y), line, fill="white", font=font(22))
        y += 45

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 *Привет!* Я бот статистики Brawl Stars.\n\n"
        "Отправь мне тег игрока в формате `#XXXXXXXX`\n"
        "и я пришлю карточку со статистикой как на sltbot!\n\n"
        "Пример: `#2GPQY9RJL`",
        parse_mode="Markdown",
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 *Как пользоваться:*\n\n"
        "1. Найдите тег в Brawl Stars (профиль → тег)\n"
        "2. Отправьте его мне: `#2GPQY9RJL`\n"
        "3. Получите карточку статистики!",
        parse_mode="Markdown",
    )

TAG_PATTERN = re.compile(r"^#?[0289PYLQGRJCUV]{3,15}$", re.IGNORECASE)

@dp.message(F.text)
async def handle_tag(message: types.Message):
    raw = message.text.strip().upper()
    if not raw.startswith("#"):
        raw = "#" + raw

    if not TAG_PATTERN.match(raw):
        await message.answer(
            "❌ Неверный формат тега.\nПример: `#2GPQY9RJL`",
            parse_mode="Markdown",
        )
        return

    wait_msg = await message.answer("⏳ Загружаю статистику…")

    # 1) Fetch player data
    try:
        data = await fetch_player(raw)
    except ValueError as e:
        await wait_msg.edit_text(f"❌ {e}")
        return
    except PermissionError as e:
        await wait_msg.edit_text(f"🔒 {e}")
        return
    except Exception as e:
        logger.exception("API error")
        await wait_msg.edit_text(f"⚠️ Ошибка API: {e}")
        return

    # 2) Get image from brawlbot.xyz (sltbot-style)
    img_bytes = None
    try:
        img_bytes = await fetch_stats_image(raw)
    except Exception as e:
        logger.warning(f"Image fetch error: {e}")

    # 3) Fallback
    if not img_bytes:
        logger.info("Fallback image")
        img_bytes = generate_fallback_image(data)

    player_name = data.get("name", "Unknown")
    caption = (
        f"📊 *{player_name}* ({raw})\n"
        f"🏆 Трофеи: {data.get('trophies', 0):,}\n"
        f"🎮 Бравлеров: {len(data.get('brawlers', []))}"
    )

    photo = BufferedInputFile(img_bytes, filename=f"stats_{raw.replace('#','')}.png")
    await message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")
    await wait_msg.delete()

    # Send to channel
    if CHANNEL_ID:
        try:
            ch_photo = BufferedInputFile(img_bytes, filename=f"stats_{raw.replace('#','')}.png")
            await bot.send_photo(chat_id=CHANNEL_ID, photo=ch_photo, caption=caption, parse_mode="Markdown")
            await message.answer("✅ Отправлено в канал!")
        except Exception as e:
            logger.warning(f"Channel send failed: {e}")
            await message.answer("⚠️ Не удалось отправить в канал.")


# ── Entry point ──────────────────────────────────────────────────────────────

async def main():
    logger.info("Bot starting…")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.ipify.org") as r:
                logger.info(f"=== SERVER IP: {await r.text()} ===")
    except Exception:
        pass

    if WEBHOOK_URL:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        from aiohttp import web

        webhook_path = f"/webhook/{TELEGRAM_TOKEN}"
        full_url = WEBHOOK_URL.rstrip("/") + webhook_path

        await bot.set_webhook(full_url)
        logger.info(f"Webhook: {full_url}")

        app = web.Application()
        async def health(_): return web.Response(text="OK")
        app.router.add_get("/", health)

        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)

        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        logger.info(f"Listening on :{PORT}")

        import asyncio
        await asyncio.Event().wait()
    else:
        logger.info("Polling mode")
        await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
