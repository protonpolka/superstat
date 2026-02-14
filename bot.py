"""
Telegram Bot for Supercell Games Statistics
Supports: Brawl Stars, Clash Royale, Clash of Clans
Flow: /start → choose game → send tag → send description → card posted to channel
"""

import os
import logging
import re
import urllib.parse
from io import BytesIO

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BRAWL_STARS_API_KEY = os.getenv("BRAWL_STARS_API_KEY")
CLASH_ROYALE_API_KEY = os.getenv("CLASH_ROYALE_API_KEY", BRAWL_STARS_API_KEY)
CLASH_OF_CLANS_API_KEY = os.getenv("CLASH_OF_CLANS_API_KEY", BRAWL_STARS_API_KEY)
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN must be set")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ── API Configuration ─────────────────────────────────────────────────────────

GAMES = {
    "bs": {
        "name": "Brawl Stars",
        "emoji": "🌟",
        "api_base": "https://bsproxy.royaleapi.dev/v1",
        "api_key_env": "BRAWL_STARS_API_KEY",
        "color": (0, 200, 80),
    },
    "cr": {
        "name": "Clash Royale",
        "emoji": "👑",
        "api_base": "https://proxy.royaleapi.dev/v1",
        "api_key_env": "CLASH_ROYALE_API_KEY",
        "color": (30, 130, 230),
    },
    "coc": {
        "name": "Clash of Clans",
        "emoji": "⚔️",
        "api_base": "https://cocproxy.royaleapi.dev/v1",
        "api_key_env": "CLASH_OF_CLANS_API_KEY",
        "color": (200, 150, 30),
    },
}

IMAGE_URLS_BS = [
    "https://sltbot.com/api/image/{tag}",
    "https://sltbot.com/api/player/{tag}/image",
    "https://sltbot.com/api/rank/{tag}",
    "https://brawltracker.com/api/image/rank/{tag}",
    "https://brawlbot.xyz/api/image/rank/{tag}",
]


# ── FSM States ────────────────────────────────────────────────────────────────

class PlayerForm(StatesGroup):
    waiting_for_game = State()
    waiting_for_tag = State()
    waiting_for_description = State()


# ── API Helpers ───────────────────────────────────────────────────────────────

def get_api_key(game_id: str) -> str:
    keys = {
        "bs": BRAWL_STARS_API_KEY,
        "cr": CLASH_ROYALE_API_KEY,
        "coc": CLASH_OF_CLANS_API_KEY,
    }
    return keys.get(game_id, "")


async def fetch_player(tag: str, game_id: str) -> dict:
    game = GAMES[game_id]
    encoded_tag = urllib.parse.quote(tag)
    url = f"{game['api_base']}/players/{encoded_tag}"
    api_key = get_api_key(game_id)
    headers = {"Authorization": f"Bearer {api_key}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            logger.info(f"{game['name']} API: {url} -> {resp.status}")
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
                logger.error(f"API {resp.status}: {text[:300]}")
                raise ConnectionError(f"Ошибка API ({resp.status})")


async def fetch_bs_image(tag: str) -> bytes | None:
    """Try to get sltbot-style image for Brawl Stars."""
    clean_tag = tag.lstrip("#")
    async with aiohttp.ClientSession() as session:
        for url_template in IMAGE_URLS_BS:
            url = url_template.format(tag=clean_tag)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    logger.info(f"TRY {url} -> {resp.status} type={ct}")
                    if resp.status == 200:
                        data = await resp.read()
                        if "image" in ct or data[:4] == b'\x89PNG' or data[:2] == b'\xff\xd8':
                            logger.info(f"SUCCESS: {url} -> {len(data)} bytes")
                            return data
            except Exception as e:
                logger.warning(f"ERROR {url}: {e}")
    return None


# ── Image Generation ──────────────────────────────────────────────────────────

def _font(size, bold=False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold \
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def generate_bs_card(data: dict) -> bytes:
    """Generate Brawl Stars stats card."""
    W, H = 800, 480
    img = Image.new("RGB", (W, H), (20, 20, 35))
    d = ImageDraw.Draw(img)

    d.rectangle([(0, 0), (W, 6)], fill=(0, 200, 80))

    name = data.get("name", "?")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    highest = data.get("highestTrophies", 0)
    v3 = data.get("3vs3Victories", 0)
    solo = data.get("soloVictories", 0)
    duo = data.get("duoVictories", 0)
    brawlers = data.get("brawlers", [])
    club = data.get("club", {}).get("name", "—")

    d.text((30, 20), "🌟 BRAWL STARS", fill=(0, 200, 80), font=_font(16, True))
    d.text((30, 48), name, fill="white", font=_font(34, True))
    d.text((30, 90), f"{tag}  •  {club}", fill=(150, 150, 170), font=_font(16))

    d.line([(30, 120), (W-30, 120)], fill=(50, 50, 70))

    y = 140
    stats = [
        ("🏆 Трофеи", f"{trophies:,}"),
        ("🏆 Рекорд", f"{highest:,}"),
        ("⚔️ 3v3 побед", f"{v3:,}"),
        ("🎯 Соло побед", f"{solo:,}"),
        ("👥 Дуо побед", f"{duo:,}"),
        ("🎮 Бравлеров", f"{len(brawlers)}"),
    ]

    col1_x, col2_x = 50, 420
    for i, (label, val) in enumerate(stats):
        x = col1_x if i % 2 == 0 else col2_x
        cy = y + (i // 2) * 55
        d.text((x, cy), label, fill=(150, 150, 170), font=_font(15))
        d.text((x, cy + 22), val, fill="white", font=_font(24, True))

    # Top brawlers
    top = sorted(brawlers, key=lambda b: b.get("trophies", 0), reverse=True)[:5]
    by = y + 180
    d.text((30, by), "ТОП БРАВЛЕРЫ", fill=(0, 200, 80), font=_font(14, True))
    for i, br in enumerate(top):
        bx = 30 + i * 150
        d.text((bx, by + 25), br.get("name", "?")[:10], fill="white", font=_font(13))
        d.text((bx, by + 43), f"🏆{br.get('trophies',0)} P{br.get('power',1)}", fill=(150,150,170), font=_font(12))

    d.text((30, H-25), "Supercell Stats Bot", fill=(60,60,80), font=_font(12))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_cr_card(data: dict) -> bytes:
    """Generate Clash Royale stats card."""
    W, H = 800, 420
    img = Image.new("RGB", (W, H), (15, 25, 50))
    d = ImageDraw.Draw(img)

    d.rectangle([(0, 0), (W, 6)], fill=(30, 130, 230))

    name = data.get("name", "?")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    best = data.get("bestTrophies", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    three_crowns = data.get("threeCrownWins", 0)
    cards_found = len(data.get("cards", []))
    level = data.get("expLevel", 0)
    arena = data.get("arena", {}).get("name", "—")
    clan = data.get("clan", {}).get("name", "—")
    donations = data.get("totalDonations", 0)
    challenge_max = data.get("challengeMaxWins", 0)

    d.text((30, 20), "👑 CLASH ROYALE", fill=(30, 130, 230), font=_font(16, True))
    d.text((30, 48), name, fill="white", font=_font(34, True))
    d.text((30, 90), f"{tag}  •  {clan}  •  {arena}", fill=(120, 140, 180), font=_font(15))

    d.line([(30, 118), (W-30, 118)], fill=(40, 50, 80))

    y = 135
    stats = [
        ("🏆 Трофеи", f"{trophies:,}"),
        ("🏆 Рекорд", f"{best:,}"),
        ("⭐ Уровень", f"{level}"),
        ("🃏 Карт найдено", f"{cards_found}"),
        ("✅ Побед", f"{wins:,}"),
        ("❌ Поражений", f"{losses:,}"),
        ("👑 3-Crown побед", f"{three_crowns:,}"),
        ("🏅 Макс челлендж", f"{challenge_max}"),
        ("🎁 Донатов", f"{donations:,}"),
    ]

    col1_x, col2_x, col3_x = 50, 300, 560
    cols = [col1_x, col2_x, col3_x]
    for i, (label, val) in enumerate(stats):
        x = cols[i % 3]
        cy = y + (i // 3) * 55
        d.text((x, cy), label, fill=(120, 140, 180), font=_font(14))
        d.text((x, cy + 20), val, fill="white", font=_font(22, True))

    d.text((30, H-25), "Supercell Stats Bot", fill=(40, 50, 80), font=_font(12))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_coc_card(data: dict) -> bytes:
    """Generate Clash of Clans stats card."""
    W, H = 800, 450
    img = Image.new("RGB", (W, H), (30, 20, 10))
    d = ImageDraw.Draw(img)

    d.rectangle([(0, 0), (W, 6)], fill=(200, 150, 30))

    name = data.get("name", "?")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    best = data.get("bestTrophies", 0)
    th_level = data.get("townHallLevel", 0)
    th_weapon = data.get("townHallWeaponLevel", 0)
    bh_level = data.get("builderHallLevel", 0)
    exp = data.get("expLevel", 0)
    war_stars = data.get("warStars", 0)
    attack_wins = data.get("attackWins", 0)
    defense_wins = data.get("defenseWins", 0)
    donations = data.get("donations", 0)
    received = data.get("donationsReceived", 0)
    clan = data.get("clan", {}).get("name", "—")
    role = data.get("role", "—")
    heroes = data.get("heroes", [])
    league = data.get("league", {}).get("name", "—")

    d.text((30, 20), "⚔️ CLASH OF CLANS", fill=(200, 150, 30), font=_font(16, True))
    d.text((30, 48), name, fill="white", font=_font(34, True))
    d.text((30, 90), f"{tag}  •  {clan} ({role})", fill=(160, 140, 100), font=_font(15))

    d.line([(30, 118), (W-30, 118)], fill=(60, 50, 30))

    y = 135
    th_text = f"{th_level}" + (f" (weapon {th_weapon})" if th_weapon else "")
    stats = [
        ("🏆 Трофеи", f"{trophies:,}"),
        ("🏆 Рекорд", f"{best:,}"),
        ("🏠 Ратуша", th_text),
        ("🏗 Мастерская", f"{bh_level}"),
        ("⭐ Уровень", f"{exp}"),
        ("🏅 Лига", league),
        ("⚔️ Атак выиграно", f"{attack_wins:,}"),
        ("🛡 Защит выиграно", f"{defense_wins:,}"),
        ("⭐ Звёзд в войнах", f"{war_stars:,}"),
        ("🎁 Донатов", f"{donations:,}"),
        ("📥 Получено", f"{received:,}"),
    ]

    col1_x, col2_x, col3_x = 50, 300, 560
    cols = [col1_x, col2_x, col3_x]
    for i, (label, val) in enumerate(stats):
        x = cols[i % 3]
        cy = y + (i // 3) * 50
        d.text((x, cy), label, fill=(160, 140, 100), font=_font(14))
        d.text((x, cy + 20), val, fill="white", font=_font(20, True))

    # Heroes
    if heroes:
        hy = y + 210
        d.text((30, hy), "ГЕРОИ", fill=(200, 150, 30), font=_font(14, True))
        for i, h in enumerate(heroes[:6]):
            hx = 30 + i * 125
            d.text((hx, hy + 22), h.get("name", "?")[:12], fill="white", font=_font(12))
            d.text((hx, hy + 38), f"Lv.{h.get('level',0)}/{h.get('maxLevel',0)}", fill=(160,140,100), font=_font(11))

    d.text((30, H-25), "Supercell Stats Bot", fill=(60, 50, 30), font=_font(12))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_username(message: types.Message) -> str:
    user = message.from_user
    if user.username:
        return f"@{user.username}"
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"
    return name or f"id:{user.id}"


def game_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌟 Brawl Stars", callback_data="game_bs"),
            InlineKeyboardButton(text="👑 Clash Royale", callback_data="game_cr"),
        ],
        [
            InlineKeyboardButton(text="⚔️ Clash of Clans", callback_data="game_coc"),
        ],
    ])


def build_caption(player_data: dict, game_id: str, tag: str, description: str, username: str) -> str:
    game = GAMES[game_id]
    name = player_data.get("name", "?")
    trophies = player_data.get("trophies", 0)

    lines = [
        f"{game['emoji']} *{game['name']}*",
        f"📊 *{name}* ({tag})",
        f"🏆 Трофеи: {trophies:,}",
    ]

    if game_id == "bs":
        lines.append(f"🎮 Бравлеров: {len(player_data.get('brawlers', []))}")
    elif game_id == "cr":
        lines.append(f"⭐ Уровень: {player_data.get('expLevel', 0)}")
        lines.append(f"🃏 Карт: {len(player_data.get('cards', []))}")
    elif game_id == "coc":
        lines.append(f"🏠 Ратуша: {player_data.get('townHallLevel', 0)}")
        lines.append(f"⭐ Уровень: {player_data.get('expLevel', 0)}")

    lines.append(f"\n📝 {description}")
    lines.append(f"👤 Отправил: {username}")

    return "\n".join(lines)


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 *Привет!* Я бот статистики Supercell.\n\n"
        "Выберите игру:",
        parse_mode="Markdown",
        reply_markup=game_keyboard(),
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 *Как пользоваться:*\n\n"
        "1. Нажмите /start\n"
        "2. Выберите игру\n"
        "3. Отправьте тег игрока\n"
        "4. Напишите описание\n"
        "5. Карточка отправится в канал!",
        parse_mode="Markdown",
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=game_keyboard())


@dp.callback_query(F.data.startswith("game_"))
async def on_game_selected(callback: types.CallbackQuery, state: FSMContext):
    game_id = callback.data.replace("game_", "")
    if game_id not in GAMES:
        await callback.answer("Неизвестная игра")
        return

    game = GAMES[game_id]
    await state.update_data(game_id=game_id)
    await state.set_state(PlayerForm.waiting_for_tag)

    await callback.message.edit_text(
        f"{game['emoji']} *{game['name']}*\n\n"
        f"Отправьте тег игрока (например `#2GPQY9RJL`):",
        parse_mode="Markdown",
    )
    await callback.answer()


TAG_PATTERN = re.compile(r"^#?[0289PYLQGRJCUV]{3,15}$", re.IGNORECASE)


@dp.message(PlayerForm.waiting_for_tag)
async def process_tag(message: types.Message, state: FSMContext):
    raw = message.text.strip().upper()
    if not raw.startswith("#"):
        raw = "#" + raw

    if not TAG_PATTERN.match(raw):
        await message.answer("❌ Неверный тег. Пример: `#2GPQY9RJL`", parse_mode="Markdown")
        return

    data = await state.get_data()
    game_id = data.get("game_id", "bs")

    wait_msg = await message.answer("⏳ Загружаю статистику…")

    try:
        player_data = await fetch_player(raw, game_id)
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

    # Generate image
    img_bytes = None

    if game_id == "bs":
        try:
            img_bytes = await fetch_bs_image(raw)
        except Exception as e:
            logger.warning(f"BS image error: {e}")
        if not img_bytes:
            img_bytes = generate_bs_card(player_data)
    elif game_id == "cr":
        img_bytes = generate_cr_card(player_data)
    elif game_id == "coc":
        img_bytes = generate_coc_card(player_data)

    await state.update_data(
        player_data=player_data,
        img_bytes=img_bytes,
        tag=raw,
    )
    await state.set_state(PlayerForm.waiting_for_description)

    player_name = player_data.get("name", "Unknown")
    trophies = player_data.get("trophies", 0)
    game = GAMES[game_id]

    extra = ""
    if game_id == "coc":
        extra = f"\n🏠 Ратуша: {player_data.get('townHallLevel', 0)}"
    elif game_id == "cr":
        extra = f"\n⭐ Уровень: {player_data.get('expLevel', 0)}"

    await wait_msg.edit_text(
        f"✅ {game['emoji']} *{player_name}* — {trophies:,} 🏆{extra}\n\n"
        f"📝 Теперь напишите описание:\n"
        f"_(или /cancel для отмены)_",
        parse_mode="Markdown",
    )


@dp.message(PlayerForm.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    description = message.text.strip()
    data = await state.get_data()
    await state.clear()

    player_data = data.get("player_data")
    img_bytes = data.get("img_bytes")
    tag = data.get("tag")
    game_id = data.get("game_id", "bs")
    username = get_username(message)

    if not player_data or not img_bytes:
        await message.answer("⚠️ Ошибка. Нажмите /start и попробуйте снова.")
        return

    caption = build_caption(player_data, game_id, tag, description, username)

    photo = BufferedInputFile(img_bytes, filename=f"stats_{tag.replace('#','')}.png")
    await message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")

    if CHANNEL_ID:
        try:
            ch = BufferedInputFile(img_bytes, filename=f"stats_{tag.replace('#','')}.png")
            await bot.send_photo(chat_id=CHANNEL_ID, photo=ch, caption=caption, parse_mode="Markdown")
            await message.answer("✅ Отправлено в канал!")
        except Exception as e:
            logger.warning(f"Channel: {e}")
            await message.answer("⚠️ Не удалось отправить в канал.")

    # Show game selection again
    await message.answer("Хотите проверить ещё аккаунт?", reply_markup=game_keyboard())


@dp.message(F.text)
async def fallback_text(message: types.Message, state: FSMContext):
    await message.answer(
        "Нажмите /start чтобы выбрать игру и отправить тег.",
        reply_markup=game_keyboard(),
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
