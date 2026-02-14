"""
Telegram Bot for Supercell Games Statistics
BS = image card, CR & CoC = text stats
Flow: /start → choose game → tag → description → posted to channel
"""

import os
import logging
import re
import urllib.parse

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

GAMES = {
    "bs": {
        "name": "Brawl Stars",
        "emoji": "🌟",
        "api_base": "https://bsproxy.royaleapi.dev/v1",
    },
    "cr": {
        "name": "Clash Royale",
        "emoji": "👑",
        "api_base": "https://proxy.royaleapi.dev/v1",
    },
    "coc": {
        "name": "Clash of Clans",
        "emoji": "⚔️",
        "api_base": "https://cocproxy.royaleapi.dev/v1",
    },
}

IMAGE_URLS_BS = [
    "https://sltbot.com/api/image/{tag}",
    "https://sltbot.com/api/player/{tag}/image",
    "https://sltbot.com/api/rank/{tag}",
    "https://brawltracker.com/api/image/rank/{tag}",
    "https://brawlbot.xyz/api/image/rank/{tag}",
]


class PlayerForm(StatesGroup):
    waiting_for_tag = State()
    waiting_for_description = State()


def get_api_key(game_id: str) -> str:
    return {"bs": BRAWL_STARS_API_KEY, "cr": CLASH_ROYALE_API_KEY, "coc": CLASH_OF_CLANS_API_KEY}.get(game_id, "")


async def fetch_player(tag: str, game_id: str) -> dict:
    game = GAMES[game_id]
    encoded_tag = urllib.parse.quote(tag)
    url = f"{game['api_base']}/players/{encoded_tag}"
    headers = {"Authorization": f"Bearer {get_api_key(game_id)}"}

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


def generate_bs_fallback(data: dict) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    from io import BytesIO

    W, H = 800, 400
    img = Image.new("RGB", (W, H), (20, 20, 35))
    d = ImageDraw.Draw(img)

    def font(size, bold=False):
        p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold \
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    d.rectangle([(0, 0), (W, 5)], fill=(0, 200, 80))
    d.text((30, 20), data.get("name", "?"), fill="white", font=font(34, True))
    d.text((30, 62), data.get("tag", ""), fill=(150, 150, 170), font=font(16))
    y = 100
    for line in [
        f"Trophies: {data.get('trophies',0):,} / {data.get('highestTrophies',0):,}",
        f"3v3: {data.get('3vs3Victories',0):,}  Solo: {data.get('soloVictories',0):,}  Duo: {data.get('duoVictories',0):,}",
        f"Brawlers: {len(data.get('brawlers',[]))}",
    ]:
        d.text((30, y), line, fill="white", font=font(22))
        y += 45

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def format_cr_text(data: dict) -> str:
    name = data.get("name", "?")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    best = data.get("bestTrophies", 0)
    level = data.get("expLevel", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    three_crowns = data.get("threeCrownWins", 0)
    cards = len(data.get("cards", []))
    clan = data.get("clan", {}).get("name", "—")
    arena = data.get("arena", {}).get("name", "—")
    donations = data.get("totalDonations", 0)
    challenge_max = data.get("challengeMaxWins", 0)

    return (
        f"👑 *CLASH ROYALE*\n\n"
        f"👤 *{name}* ({tag})\n"
        f"🏠 Клан: {clan}\n"
        f"🏟 Арена: {arena}\n\n"
        f"🏆 Трофеи: {trophies:,}\n"
        f"🏆 Рекорд: {best:,}\n"
        f"⭐ Уровень: {level}\n"
        f"🃏 Карт найдено: {cards}\n\n"
        f"✅ Побед: {wins:,}\n"
        f"❌ Поражений: {losses:,}\n"
        f"👑 3-Crown побед: {three_crowns:,}\n"
        f"🏅 Макс челлендж: {challenge_max}\n"
        f"🎁 Всего донатов: {donations:,}"
    )


def format_coc_text(data: dict) -> str:
    name = data.get("name", "?")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    best = data.get("bestTrophies", 0)
    th = data.get("townHallLevel", 0)
    th_weapon = data.get("townHallWeaponLevel", 0)
    bh = data.get("builderHallLevel", 0)
    exp = data.get("expLevel", 0)
    war_stars = data.get("warStars", 0)
    attack_wins = data.get("attackWins", 0)
    defense_wins = data.get("defenseWins", 0)
    donations = data.get("donations", 0)
    received = data.get("donationsReceived", 0)
    clan = data.get("clan", {}).get("name", "—")
    role = data.get("role", "—")
    league = data.get("league", {}).get("name", "—")
    heroes = data.get("heroes", [])

    th_text = f"{th}" + (f" (оружие {th_weapon})" if th_weapon else "")

    hero_lines = ""
    if heroes:
        hero_lines = "\n🦸 *Герои:*\n"
        for h in heroes:
            hero_lines += f"  • {h.get('name','?')}: Lv.{h.get('level',0)}/{h.get('maxLevel',0)}\n"

    return (
        f"⚔️ *CLASH OF CLANS*\n\n"
        f"👤 *{name}* ({tag})\n"
        f"🏠 Клан: {clan} ({role})\n"
        f"🏅 Лига: {league}\n\n"
        f"🏠 Ратуша: {th_text}\n"
        f"🏗 Мастерская: {bh}\n"
        f"⭐ Уровень: {exp}\n\n"
        f"🏆 Трофеи: {trophies:,}\n"
        f"🏆 Рекорд: {best:,}\n\n"
        f"⚔️ Атак выиграно: {attack_wins:,}\n"
        f"🛡 Защит выиграно: {defense_wins:,}\n"
        f"⭐ Звёзд в войнах: {war_stars:,}\n\n"
        f"🎁 Донатов: {donations:,}\n"
        f"📥 Получено: {received:,}"
        f"{hero_lines}"
    )


def get_username(msg: types.Message) -> str:
    u = msg.from_user
    if u.username:
        return f"@{u.username}"
    name = u.first_name or ""
    if u.last_name:
        name += f" {u.last_name}"
    return name or f"id:{u.id}"


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


# ── Handlers ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 *Выберите игру:*", parse_mode="Markdown", reply_markup=game_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "1. /start → выберите игру\n2. Отправьте тег\n3. Напишите описание\n4. Готово!",
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=game_keyboard())


@dp.callback_query(F.data.startswith("game_"))
async def on_game_selected(cb: types.CallbackQuery, state: FSMContext):
    game_id = cb.data.replace("game_", "")
    if game_id not in GAMES:
        await cb.answer("Неизвестная игра")
        return
    game = GAMES[game_id]
    await state.update_data(game_id=game_id)
    await state.set_state(PlayerForm.waiting_for_tag)
    await cb.message.edit_text(
        f"{game['emoji']} *{game['name']}*\n\nОтправьте тег игрока:",
        parse_mode="Markdown",
    )
    await cb.answer()


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
    wait_msg = await message.answer("⏳ Загружаю…")

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
        await wait_msg.edit_text(f"⚠️ {e}")
        return

    # For BS — fetch image
    img_bytes = None
    if game_id == "bs":
        try:
            img_bytes = await fetch_bs_image(raw)
        except Exception:
            pass
        if not img_bytes:
            img_bytes = generate_bs_fallback(player_data)

    await state.update_data(player_data=player_data, img_bytes=img_bytes, tag=raw)
    await state.set_state(PlayerForm.waiting_for_description)

    name = player_data.get("name", "?")
    trophies = player_data.get("trophies", 0)
    game = GAMES[game_id]

    extra = ""
    if game_id == "coc":
        extra = f"\n🏠 Ратуша: {player_data.get('townHallLevel', 0)}"
    elif game_id == "cr":
        extra = f"\n⭐ Уровень: {player_data.get('expLevel', 0)}"

    await wait_msg.edit_text(
        f"✅ {game['emoji']} *{name}* — {trophies:,} 🏆{extra}\n\n"
        f"📝 Напишите описание:\n_(или /cancel)_",
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
    game = GAMES[game_id]

    if not player_data:
        await message.answer("⚠️ Ошибка. /start")
        return

    footer = f"\n\n📝 {description}\n👤 Отправил: {username}"

    if game_id == "bs":
        # ── Brawl Stars: фото + подпись ──
        name = player_data.get("name", "?")
        trophies = player_data.get("trophies", 0)
        brawlers = len(player_data.get("brawlers", []))

        caption = (
            f"🌟 *BRAWL STARS*\n"
            f"📊 *{name}* ({tag})\n"
            f"🏆 Трофеи: {trophies:,}\n"
            f"🎮 Бравлеров: {brawlers}"
            f"{footer}"
        )

        photo = BufferedInputFile(img_bytes, filename=f"bs_{tag.replace('#','')}.png")
        await message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")

        if CHANNEL_ID:
            try:
                ch = BufferedInputFile(img_bytes, filename=f"bs_{tag.replace('#','')}.png")
                await bot.send_photo(chat_id=CHANNEL_ID, photo=ch, caption=caption, parse_mode="Markdown")
                await message.answer("✅ Отправлено в канал!")
            except Exception as e:
                logger.warning(f"Channel: {e}")
                await message.answer("⚠️ Не удалось отправить в канал.")

    elif game_id == "cr":
        # ── Clash Royale: текст ──
        text = format_cr_text(player_data) + footer
        await message.answer(text, parse_mode="Markdown")

        if CHANNEL_ID:
            try:
                await bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
                await message.answer("✅ Отправлено в канал!")
            except Exception as e:
                logger.warning(f"Channel: {e}")
                await message.answer("⚠️ Не удалось отправить в канал.")

    elif game_id == "coc":
        # ── Clash of Clans: текст ──
        text = format_coc_text(player_data) + footer
        await message.answer(text, parse_mode="Markdown")

        if CHANNEL_ID:
            try:
                await bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
                await message.answer("✅ Отправлено в канал!")
            except Exception as e:
                logger.warning(f"Channel: {e}")
                await message.answer("⚠️ Не удалось отправить в канал.")

    await message.answer("Ещё аккаунт?", reply_markup=game_keyboard())


@dp.message(F.text)
async def fallback(message: types.Message):
    await message.answer("Нажмите /start", reply_markup=game_keyboard())


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
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler
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
