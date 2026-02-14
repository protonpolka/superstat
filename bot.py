"""
Telegram Bot for Brawl Stars Player Statistics
Fetches player data from Supercell API, generates a stats image,
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
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BRAWL_STARS_API_KEY = os.getenv("BRAWL_STARS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")          # e.g. @my_channel or -100123456789
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")     # Railway provides this

if not TELEGRAM_TOKEN or not BRAWL_STARS_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN and BRAWL_STARS_API_KEY must be set in .env")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Bot / Dispatcher ─────────────────────────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ── Brawl Stars API helpers ──────────────────────────────────────────────────
BS_API_BASE = "https://api.brawlstars.com/v1"

BRAWLER_COLORS = {
    "Trophy Road": "#f5c542",
    "Rare":        "#58d68d",
    "Super Rare":  "#5dade2",
    "Epic":        "#a569bd",
    "Mythic":      "#e74c3c",
    "Legendary":   "#f9e79f",
}

# Color palette for the card
COLORS = {
    "bg_top":       (30,  30,  46),
    "bg_bottom":    (24,  24,  37),
    "accent":       (250, 200, 60),
    "accent2":      (100, 200, 255),
    "text_white":   (255, 255, 255),
    "text_gray":    (160, 160, 180),
    "text_gold":    (255, 215, 0),
    "card_bg":      (40,  40,  58),
    "card_border":  (60,  60,  80),
    "bar_bg":       (50,  50,  70),
    "bar_fill":     (250, 200, 60),
    "bar_fill_alt": (100, 200, 255),
    "divider":      (55,  55,  75),
}


async def fetch_player(tag: str) -> dict:
    """Fetch player data from the Brawl Stars API."""
    encoded_tag = urllib.parse.quote(tag)
    url = f"{BS_API_BASE}/players/{encoded_tag}"
    headers = {"Authorization": f"Bearer {BRAWL_STARS_API_KEY}"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 404:
                raise ValueError("Игрок не найден. Проверьте тег.")
            elif resp.status == 403:
                raise PermissionError("Ошибка авторизации API. Проверьте ключ и IP.")
            else:
                text = await resp.text()
                raise ConnectionError(f"Ошибка API ({resp.status}): {text[:200]}")


# ── Image Generation ─────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load a good-looking font, fall back to default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _draw_rounded_rect(draw: ImageDraw.Draw, xy, radius, fill, outline=None):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)


def _draw_progress_bar(draw: ImageDraw.Draw, x, y, w, h, ratio, color_fill, color_bg):
    """Draw a horizontal progress bar."""
    _draw_rounded_rect(draw, (x, y, x + w, y + h), radius=h // 2, fill=color_bg)
    fill_w = max(h, int(w * min(ratio, 1.0)))
    _draw_rounded_rect(draw, (x, y, x + fill_w, y + h), radius=h // 2, fill=color_fill)


def _draw_stat_block(draw, x, y, label, value, font_label, font_value, color_label, color_value):
    """Draw a label + value vertically centered block."""
    draw.text((x, y), label, fill=color_label, font=font_label)
    draw.text((x, y + 26), str(value), fill=color_value, font=font_value)


def generate_stats_image(data: dict) -> bytes:
    """Generate a beautiful stats card for a Brawl Stars player."""
    W, H = 800, 620
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # ── Background gradient (simulated) ──
    for y_line in range(H):
        ratio = y_line / H
        r = int(COLORS["bg_top"][0] * (1 - ratio) + COLORS["bg_bottom"][0] * ratio)
        g = int(COLORS["bg_top"][1] * (1 - ratio) + COLORS["bg_bottom"][1] * ratio)
        b = int(COLORS["bg_top"][2] * (1 - ratio) + COLORS["bg_bottom"][2] * ratio)
        draw.line([(0, y_line), (W, y_line)], fill=(r, g, b))

    # ── Fonts ──
    font_title = _load_font(36, bold=True)
    font_tag = _load_font(18)
    font_label = _load_font(16)
    font_value = _load_font(24, bold=True)
    font_section = _load_font(20, bold=True)
    font_small = _load_font(14)

    # ── Extract data ──
    name = data.get("name", "Unknown")
    tag = data.get("tag", "")
    trophies = data.get("trophies", 0)
    highest_trophies = data.get("highestTrophies", 0)
    exp_level = data.get("expLevel", 0)
    exp_points = data.get("expPoints", 0)
    victories_3v3 = data.get("3vs3Victories", 0)
    solo_victories = data.get("soloVictories", 0)
    duo_victories = data.get("duoVictories", 0)
    best_robo = data.get("bestRoboRumbleTime", 0)
    club_name = data.get("club", {}).get("name", "Нет клуба")
    brawlers = data.get("brawlers", [])
    name_color = data.get("nameColor", "0xffffffff")

    total_wins = victories_3v3 + solo_victories + duo_victories

    # ── Header area ──
    # Accent line
    draw.rectangle([(0, 0), (W, 5)], fill=COLORS["accent"])

    # Player name
    draw.text((40, 28), name, fill=COLORS["text_white"], font=font_title)
    # Tag
    draw.text((40, 72), tag, fill=COLORS["text_gray"], font=font_tag)
    # Club
    draw.text((40, 96), f"🏠 {club_name}", fill=COLORS["text_gray"], font=font_tag)

    # Exp level badge (right side)
    badge_x, badge_y = W - 120, 30
    _draw_rounded_rect(draw, (badge_x, badge_y, badge_x + 80, badge_y + 60),
                       radius=12, fill=COLORS["card_bg"], outline=COLORS["accent"])
    draw.text((badge_x + 14, badge_y + 4), "LVL", fill=COLORS["text_gray"], font=font_small)
    lvl_text = str(exp_level)
    bbox = draw.textbbox((0, 0), lvl_text, font=font_value)
    tw = bbox[2] - bbox[0]
    draw.text((badge_x + (80 - tw) // 2, badge_y + 24), lvl_text,
              fill=COLORS["accent"], font=font_value)

    # ── Divider ──
    draw.line([(30, 130), (W - 30, 130)], fill=COLORS["divider"], width=1)

    # ── Trophies section ──
    y_sec = 148
    draw.text((40, y_sec), "🏆 ТРОФЕИ", fill=COLORS["accent"], font=font_section)

    # Current trophies
    _draw_rounded_rect(draw, (40, y_sec + 35, 380, y_sec + 100),
                       radius=12, fill=COLORS["card_bg"])
    draw.text((60, y_sec + 42), "Текущие", fill=COLORS["text_gray"], font=font_label)
    draw.text((60, y_sec + 62), f"{trophies:,}", fill=COLORS["text_gold"], font=font_value)

    # Highest trophies
    _draw_rounded_rect(draw, (410, y_sec + 35, W - 40, y_sec + 100),
                       radius=12, fill=COLORS["card_bg"])
    draw.text((430, y_sec + 42), "Рекорд", fill=COLORS["text_gray"], font=font_label)
    draw.text((430, y_sec + 62), f"{highest_trophies:,}", fill=COLORS["text_gold"], font=font_value)

    # Trophy progress bar
    trophy_ratio = trophies / max(highest_trophies, 1)
    _draw_progress_bar(draw, 40, y_sec + 114, W - 80, 14, trophy_ratio,
                       COLORS["bar_fill"], COLORS["bar_bg"])

    # ── Victories section ──
    y_vic = y_sec + 150
    draw.text((40, y_vic), "⚔️ ПОБЕДЫ", fill=COLORS["accent2"], font=font_section)

    col_w = (W - 80 - 30) // 4  # 4 columns
    stats = [
        ("Всего",  total_wins),
        ("3 vs 3", victories_3v3),
        ("Соло",   solo_victories),
        ("Дуо",    duo_victories),
    ]
    for i, (label, val) in enumerate(stats):
        bx = 40 + i * (col_w + 10)
        _draw_rounded_rect(draw, (bx, y_vic + 35, bx + col_w, y_vic + 105),
                           radius=12, fill=COLORS["card_bg"])
        _draw_stat_block(draw, bx + 14, y_vic + 42, label, f"{val:,}",
                         font_label, font_value, COLORS["text_gray"], COLORS["text_white"])

    # ── Brawlers section ──
    y_brawl = y_vic + 130
    draw.text((40, y_brawl), f"🎮 БРАВЛЕРЫ: {len(brawlers)}", fill=COLORS["accent"], font=font_section)

    # Top-5 brawlers by trophies
    sorted_brawlers = sorted(brawlers, key=lambda b: b.get("trophies", 0), reverse=True)[:5]
    bar_y = y_brawl + 38
    max_br_trophies = sorted_brawlers[0].get("trophies", 1) if sorted_brawlers else 1

    for i, br in enumerate(sorted_brawlers):
        br_name = br.get("name", "?")
        br_trophies = br.get("trophies", 0)
        br_power = br.get("power", 1)
        cy = bar_y + i * 36

        # Name
        draw.text((55, cy + 2), br_name, fill=COLORS["text_white"], font=font_small)
        # Power
        draw.text((200, cy + 2), f"P{br_power}", fill=COLORS["text_gray"], font=font_small)
        # Bar
        ratio = br_trophies / max(max_br_trophies, 1)
        bar_x = 250
        bar_w = W - 80 - bar_x
        _draw_progress_bar(draw, bar_x, cy + 4, bar_w, 16, ratio,
                           COLORS["bar_fill_alt"], COLORS["bar_bg"])
        # Trophy count on bar
        draw.text((bar_x + 8, cy + 3), str(br_trophies),
                  fill=COLORS["bg_top"], font=font_small)

    # ── Footer ──
    draw.line([(30, H - 40), (W - 30, H - 40)], fill=COLORS["divider"], width=1)
    draw.text((40, H - 32), "Brawl Stars Stats Bot  •  data by Supercell API",
              fill=COLORS["text_gray"], font=font_small)

    # ── Export ──
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG", quality=95)
    return buf.getvalue()


# ── Telegram Handlers ────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 *Привет!* Я бот статистики Brawl Stars.\n\n"
        "Отправь мне тег игрока в формате `#XXXXXXXX`\n"
        "и я пришлю красивую карточку со статистикой!\n\n"
        "Пример: `#2PP`",
        parse_mode="Markdown",
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 *Как пользоваться:*\n\n"
        "1. Найдите тег игрока в Brawl Stars (профиль → тег)\n"
        "2. Отправьте его мне, например: `#2PP`\n"
        "3. Получите карточку статистики!\n\n"
        "Карточка также будет отправлена в канал (если настроен).",
        parse_mode="Markdown",
    )


TAG_PATTERN = re.compile(r"^#?[0289PYLQGRJCUV]{3,12}$", re.IGNORECASE)


@dp.message(F.text)
async def handle_tag(message: types.Message):
    raw = message.text.strip().upper()
    if not raw.startswith("#"):
        raw = "#" + raw

    if not TAG_PATTERN.match(raw):
        await message.answer(
            "❌ Неверный формат тега.\n"
            "Тег содержит только символы: `0289PYLQGRJCUV`\n"
            "Пример: `#2PP`",
            parse_mode="Markdown",
        )
        return

    wait_msg = await message.answer("⏳ Загружаю статистику…")

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
        await wait_msg.edit_text(f"⚠️ Ошибка при запросе к API: {e}")
        return

    try:
        img_bytes = generate_stats_image(data)
    except Exception as e:
        logger.exception("Image generation error")
        await wait_msg.edit_text("⚠️ Ошибка при генерации изображения.")
        return

    photo = BufferedInputFile(img_bytes, filename=f"stats_{raw.replace('#', '')}.png")
    player_name = data.get("name", "Unknown")

    caption = (
        f"📊 *{player_name}* ({raw})\n"
        f"🏆 Трофеи: {data.get('trophies', 0):,}\n"
        f"🎮 Бравлеров: {len(data.get('brawlers', []))}"
    )

    # Send to user
    await message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")
    await wait_msg.delete()

    # Send to channel if configured
    if CHANNEL_ID:
        try:
            channel_photo = BufferedInputFile(img_bytes, filename=f"stats_{raw.replace('#', '')}.png")
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=channel_photo,
                caption=caption,
                parse_mode="Markdown",
            )
            await message.answer("✅ Статистика также отправлена в канал!")
        except Exception as e:
            logger.warning(f"Failed to send to channel: {e}")
            await message.answer("⚠️ Не удалось отправить в канал. Проверьте настройки.")


# ── Entry point ──────────────────────────────────────────────────────────────

async def main():
    """Start the bot (polling for local dev, webhook for Railway)."""
    logger.info("Bot starting…")

    if WEBHOOK_URL:
        # ── Webhook mode (Railway / production) ──
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
        from aiohttp import web

        webhook_path = f"/webhook/{TELEGRAM_TOKEN}"
        full_webhook_url = WEBHOOK_URL.rstrip("/") + webhook_path

        await bot.set_webhook(full_webhook_url)
        logger.info(f"Webhook set: {full_webhook_url}")

        app = web.Application()
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=webhook_path)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info(f"Web server listening on port {PORT}")

        # Keep running
        import asyncio
        await asyncio.Event().wait()
    else:
        # ── Polling mode (local dev) ──
        logger.info("Running in polling mode (no WEBHOOK_URL set)")
        await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
