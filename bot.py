"""
Telegram Bot for Brawl Stars Player Statistics
After user sends a tag, bot asks for a description,
then posts the stats card + description + username to a channel.
"""

import os
import logging
import re
import urllib.parse

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BRAWL_STARS_API_KEY = os.getenv("BRAWL_STARS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

if not TELEGRAM_TOKEN or not BRAWL_STARS_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN and BRAWL_STARS_API_KEY must be set in .env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

BS_API_BASE = "https://bsproxy.royaleapi.dev/v1"

IMAGE_URLS = [
    "https://sltbot.com/api/image/{tag}",
    "https://sltbot.com/api/player/{tag}/image",
    "https://sltbot.com/api/rank/{tag}",
    "https://brawltracker.com/api/image/rank/{tag}",
    "https://brawlbot.xyz/api/image/rank/{tag}",
    "https://brawlbot.xyz/api/player/{tag}/image",
]


# ── FSM States ────────────────────────────────────────────────────────────────

class PlayerForm(StatesGroup):
    waiting_for_tag = State()
    waiting_for_description = State()


# ── API helpers ───────────────────────────────────────────────────────────────

async def fetch_player(tag: str) -> dict:
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
    clean_tag = tag.lstrip("#")

    async with aiohttp.ClientSession() as session:
        for url_template in IMAGE_URLS:
            url = url_template.format(tag=clean_tag)
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=True,
                ) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    logger.info(f"TRY {url} -> {resp.status} type={ct}")

                    if resp.status == 200:
                        data = await resp.read()
                        if ("image" in ct
                            or data[:4] == b'\x89PNG'
                            or data[:2] == b'\xff\xd8'
                            or data[:4] == b'RIFF'):
                            logger.info(f"SUCCESS: {url} -> {len(data)} bytes")
                            return data
                        else:
                            logger.info(f"NOT IMAGE: {url} -> first 200: {data[:200]}")
                    else:
                        body = await resp.read()
                        logger.info(f"FAIL {url} -> {resp.status}, body: {body[:200]}")
            except Exception as e:
                logger.warning(f"ERROR {url}: {e}")
    return None


def generate_fallback_image(data: dict) -> bytes:
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

    draw.rectangle([(0, 0), (W, 5)], fill=(250, 200, 60))
    draw.text((40, 25), data.get("name", "?"), fill="white", font=font(36, True))
    draw.text((40, 70), data.get("tag", ""), fill=(160, 160, 180), font=font(18))
    y = 110
    for line in [
        f"Trophies: {data.get('trophies',0):,} / {data.get('highestTrophies',0):,}",
        f"3v3: {data.get('3vs3Victories',0):,}  Solo: {data.get('soloVictories',0):,}  Duo: {data.get('duoVictories',0):,}",
        f"Brawlers: {len(data.get('brawlers',[]))}",
    ]:
        draw.text((40, y), line, fill="white", font=font(22))
        y += 45

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_username(message: types.Message) -> str:
    """Get display name for the user."""
    user = message.from_user
    if user.username:
        return f"@{user.username}"
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"
    return name or f"id:{user.id}"


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 *Привет!* Я бот статистики Brawl Stars.\n\n"
        "Отправь тег игрока в формате `#XXXXXXXX`",
        parse_mode="Markdown",
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 *Как пользоваться:*\n\n"
        "1. Отправьте тег игрока: `#2GPQY9RJL`\n"
        "2. Бот попросит описание\n"
        "3. Карточка будет отправлена в канал!",
        parse_mode="Markdown",
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено. Отправьте новый тег когда будете готовы.")


TAG_PATTERN = re.compile(r"^#?[0289PYLQGRJCUV]{3,15}$", re.IGNORECASE)


@dp.message(PlayerForm.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    """User sent a description — now generate and send everything."""
    description = message.text.strip()
    data = await state.get_data()
    await state.clear()

    player_data = data.get("player_data")
    img_bytes = data.get("img_bytes")
    tag = data.get("tag")
    username = get_username(message)

    if not player_data or not img_bytes:
        await message.answer("⚠️ Ошибка. Попробуйте снова — отправьте тег.")
        return

    player_name = player_data.get("name", "Unknown")
    trophies = player_data.get("trophies", 0)
    brawlers_count = len(player_data.get("brawlers", []))

    caption = (
        f"📊 *{player_name}* ({tag})\n"
        f"🏆 Трофеи: {trophies:,}\n"
        f"🎮 Бравлеров: {brawlers_count}\n\n"
        f"📝 {description}\n\n"
        f"👤 Отправил: {username}"
    )

    # Send to user
    photo = BufferedInputFile(img_bytes, filename=f"stats_{tag.replace('#','')}.png")
    await message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")

    # Send to channel
    if CHANNEL_ID:
        try:
            ch = BufferedInputFile(img_bytes, filename=f"stats_{tag.replace('#','')}.png")
            await bot.send_photo(chat_id=CHANNEL_ID, photo=ch, caption=caption, parse_mode="Markdown")
            await message.answer("✅ Отправлено в канал!")
        except Exception as e:
            logger.warning(f"Channel: {e}")
            await message.answer("⚠️ Не удалось отправить в канал.")


@dp.message(F.text)
async def handle_tag(message: types.Message, state: FSMContext):
    """User sends a tag — fetch data, then ask for description."""
    raw = message.text.strip().upper()
    if not raw.startswith("#"):
        raw = "#" + raw

    if not TAG_PATTERN.match(raw):
        await message.answer("❌ Неверный тег. Пример: `#2GPQY9RJL`", parse_mode="Markdown")
        return

    wait_msg = await message.answer("⏳ Загружаю статистику…")

    # Fetch player data
    try:
        player_data = await fetch_player(raw)
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

    # Fetch image
    img_bytes = None
    try:
        img_bytes = await fetch_stats_image(raw)
    except Exception as e:
        logger.warning(f"Image error: {e}")

    if not img_bytes:
        img_bytes = generate_fallback_image(player_data)

    # Save to FSM state and ask for description
    await state.update_data(
        player_data=player_data,
        img_bytes=img_bytes,
        tag=raw,
    )
    await state.set_state(PlayerForm.waiting_for_description)

    player_name = player_data.get("name", "Unknown")
    trophies = player_data.get("trophies", 0)

    await wait_msg.edit_text(
        f"✅ Найден: *{player_name}* — {trophies:,} 🏆\n\n"
        f"📝 Теперь напишите почту:пароль к аккаунту:\n"
        f"_(или /cancel для отмены)_",
        parse_mode="Markdown",
    )


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
