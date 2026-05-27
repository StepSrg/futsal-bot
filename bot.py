import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import BOT_TOKEN, DB_PATH
from db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = Database(DB_PATH)

# ─── MAIN MENU KEYBOARD ────────────────────────────────────────────────

def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🏐 Новая тренировка")
    kb.button(text="📊 Состав")
    kb.button(text="🏆 Рейтинг")
    kb.button(text="⚽ Моя статистика")
    kb.button(text="⚡ Матч")
    kb.button(text="📋 История матчей")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)

# ─── SET COMMANDS ──────────────────────────────────────────────────────

async def set_bot_commands():
    cmds = [
        BotCommand(command="training", description="Создать тренировку"),
        BotCommand(command="stats", description="Моя статистика"),
        BotCommand(command="top", description="Рейтинг команды"),
        BotCommand(command="roster", description="Состав команды"),
        BotCommand(command="match", description="Записать матч"),
        BotCommand(command="score", description="Результат матча"),
        BotCommand(command="matches", description="История матчей"),
        BotCommand(command="poll", description="Создать опрос"),
        BotCommand(command="help", description="Помощь"),
    ]
    await bot.set_my_commands(cmds, scope=BotCommandScopeDefault())

# ─── HELPERS ───────────────────────────────────────────────────────────

def training_keyboard(training_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Буду", callback_data=f"att_yes_{training_id}")
    kb.button(text="❌ Не буду", callback_data=f"att_no_{training_id}")
    kb.button(text="🤔 Под вопросом", callback_data=f"att_maybe_{training_id}")
    kb.adjust(3)
    return kb.as_markup()

def format_training(t):
    date_str = datetime.strptime(t["date"], "%Y-%m-%d").strftime("%d.%m")
    return f"🏐 <b>Тренировка</b>\n📅 {date_str} | {t['time']}\n📍 {t['location']}"

def parse_date(s: str) -> str | None:
    s = s.strip().replace(".", ".").replace("/", ".")
    parts = s.split(".")
    try:
        if len(parts) == 2:  # DD.MM
            now = datetime.now()
            d = int(parts[0])
            m = int(parts[1])
            y = now.year
            parsed = datetime(y, m, d)
            if parsed < now.replace(hour=0, minute=0, second=0):
                parsed = parsed.replace(year=y + 1)
            return parsed.strftime("%Y-%m-%d")
        elif len(parts) == 3:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
            parsed = datetime(y, m, d)
            return parsed.strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return None
    return None

# ─── REGISTER ──────────────────────────────────────────────────────────

async def ensure_player(tg_id: int, name: str) -> bool:
    p = await db.get_player(tg_id)
    if not p:
        await db.register_player(tg_id, name)
        return True
    return False

# ─── START / HELP ──────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок")
    await message.answer(
        "👋 <b>Футзальная команда</b>\n\n"
        "Я помогу с тренировками, опросами и статистикой.\n\n"
        "Просто жми кнопки меню или пиши команды.",
        reply_markup=main_menu(),
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📋 <b>Команды:</b>\n\n"
        "/training ДД.ММ [ЧЧ:ММ] — созвать на тренировку\n"
        "/stats — моя посещаемость\n"
        "/top — рейтинг всех\n"
        "/roster — состав команды\n"
        "/match ДД.ММ Соперник — записать игру\n"
        "/score ID СчетНаш СчетИх — результат\n"
        "/matches — история матчей\n"
        "/poll Вопрос | Вариант1 | Вариант2 — опрос",
        reply_markup=main_menu(),
    )

# ─── MENU BUTTONS ──────────────────────────────────────────────────────

@dp.message(lambda msg: msg.text == "🏐 Новая тренировка")
async def menu_training(message: types.Message):
    await message.answer(
        "Напиши: /training ДД.ММ [ЧЧ:ММ]\n"
        "Например: /training 30.05 19:00"
    )

@dp.message(lambda msg: msg.text == "📊 Состав")
async def menu_roster(message: types.Message):
    await cmd_roster(message)

@dp.message(lambda msg: msg.text == "🏆 Рейтинг")
async def menu_top(message: types.Message):
    await cmd_top(message)

@dp.message(lambda msg: msg.text == "⚽ Моя статистика")
async def menu_stats(message: types.Message):
    await cmd_stats(message)

@dp.message(lambda msg: msg.text == "⚡ Матч")
async def menu_match(message: types.Message):
    await message.answer(
        "Напиши: /match ДД.ММ Название соперника\n"
        "Например: /match 01.06 Старт"
    )

@dp.message(lambda msg: msg.text == "📋 История матчей")
async def menu_matches(message: types.Message):
    await cmd_matches(message)

# ─── TRAINING ──────────────────────────────────────────────────────────

@dp.message(Command("training"))
async def cmd_training(message: types.Message):
    await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок")

    args = message.text.replace("/training", "").strip().split(maxsplit=2)
    if not args:
        await message.answer(
            "Напиши: /training ДД.ММ [ЧЧ:ММ] [📍]\n"
            "Например: /training 30.05 19:00 Зал №3"
        )
        return

    db_date = parse_date(args[0])
    if not db_date:
        await message.answer("❌ Неверная дата. Используй ДД.ММ или ДД.ММ.ГГГГ")
        return

    time_str = args[1] if len(args) > 1 else "20:00"
    location = args[2] if len(args) > 2 else "Обычное место"

    training_id = await db.create_training(db_date, time_str, location)
    t = await db.get_training(training_id)
    text = format_training(t) + "\n\n👇 <b>Отметься:</b>"
    await message.answer(text, reply_markup=training_keyboard(training_id))

# ─── ATTENDANCE CALLBACKS ──────────────────────────────────────────────

@dp.callback_query(lambda c: c.data.startswith("att_"))
async def attendance_callback(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    status = parts[1]
    training_id = int(parts[2])
    tg_id = callback.from_user.id
    name = callback.from_user.full_name or "Игрок"
    await ensure_player(tg_id, name)
    await db.set_attendance(training_id, tg_id, status)

    labels = {"yes": "✅ Буду", "no": "❌ Не буду", "maybe": "🤔 Под вопросом"}
    await callback.answer(f"Ты отмечен: {labels[status]}", show_alert=False)

    attendance = await db.get_training_attendance(training_id)
    yes = [a for a in attendance if a["status"] == "yes"]
    no = [a for a in attendance if a["status"] == "no"]
    maybe = [a for a in attendance if a["status"] == "maybe"]

    t = await db.get_training(training_id)
    lines = [format_training(t), ""]
    lines.append(f"✅ <b>Идут ({len(yes)}):</b>")
    for a in yes:
        n = a["name"]
        n += f" ({a['nickname']})" if a["nickname"] else ""
        lines.append(f"  • {n}")
    lines.append("")
    lines.append(f"🤔 <b>Под вопросом ({len(maybe)}):</b>")
    for a in maybe:
        lines.append(f"  • {a['name']}")
    lines.append("")
    lines.append(f"❌ <b>Не идут ({len(no)}):</b>")
    for a in no:
        lines.append(f"  • {a['name']}")

    await callback.message.edit_text("\n".join(lines), reply_markup=training_keyboard(training_id))

# ─── STATS ──────────────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    tg_id = message.from_user.id
    await ensure_player(tg_id, message.from_user.full_name or "Игрок")
    stats = await db.get_player_stats(tg_id)
    if not stats or stats["total"] == 0:
        await message.answer("📊 У тебя пока нет статистики. Отметься на тренировках!", reply_markup=main_menu())
        return

    pct = round(stats["yes"] / stats["total"] * 100) if stats["total"] else 0
    await message.answer(
        f"📊 <b>Твоя статистика</b>\n"
        f"Всего опросов: {stats['total']}\n"
        f"✅ Был: {stats['yes']} ({pct}%)\n"
        f"❌ Не был: {stats['no']}\n"
        f"🤔 Под вопросом: {stats['maybe']}",
        reply_markup=main_menu(),
    )

# ─── TOP ────────────────────────────────────────────────────────────────

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    top = await db.get_top_attendance(20)
    if not top:
        await message.answer("Пока нет данных для рейтинга.", reply_markup=main_menu())
        return

    lines = ["🏆 <b>Рейтинг посещаемости</b>\n"]
    for i, p in enumerate(top, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        name = p["name"]
        lines.append(f"{medal} {name} — {p['yes']} раз")
    await message.answer("\n".join(lines), reply_markup=main_menu())

# ─── ROSTER ─────────────────────────────────────────────────────────────

@dp.message(Command("roster"))
async def cmd_roster(message: types.Message):
    players = await db.get_all_players()
    if not players:
        await message.answer("Команда пока пуста.", reply_markup=main_menu())
        return

    lines = [f"👥 <b>Состав команды ({len(players)}):</b>\n"]
    for p in players:
        name = p["name"]
        if p["nickname"]:
            name += f" ({p['nickname']})"
        lines.append(f"  • {name}")
    await message.answer("\n".join(lines), reply_markup=main_menu())

# ─── MATCH ──────────────────────────────────────────────────────────────

@dp.message(Command("match"))
async def cmd_match(message: types.Message):
    await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок")
    text = message.text.replace("/match", "").strip()
    if not text:
        await message.answer("Использование: /match ДД.ММ Название соперника")
        return

    parts = text.split(maxsplit=1)
    db_date = parse_date(parts[0])
    if not db_date:
        await message.answer("❌ Неверная дата.")
        return

    opponent = parts[1] if len(parts) > 1 else "Соперник"
    match_id = await db.create_match(db_date, opponent)
    date_str = datetime.strptime(db_date, "%Y-%m-%d").strftime("%d.%m")
    await message.answer(
        f"⚽ <b>Матч создан!</b>\n"
        f"📅 {date_str} vs {opponent}\n\n"
        f"После игры: /score {match_id} НашСчет ИхСчет",
        reply_markup=main_menu(),
    )

@dp.message(Command("score"))
async def cmd_score(message: types.Message):
    args = message.text.replace("/score", "").strip().split()
    if len(args) < 3:
        await message.answer("Использование: /score ID НашСчет ИхСчет")
        return
    try:
        match_id = int(args[0])
        our = int(args[1])
        their = int(args[2])
    except ValueError:
        await message.answer("❌ Счета должны быть числами.")
        return

    m = await db.get_match(match_id)
    if not m:
        await message.answer("❌ Матч не найден.")
        return

    await db.set_match_score(match_id, our, their)
    result = "🟢 Победа" if our > their else ("🔴 Поражение" if our < their else "🟡 Ничья")
    await message.answer(
        f"{result}!\n{m['opponent']} — {our}:{their}",
        reply_markup=main_menu(),
    )

@dp.message(Command("matches"))
async def cmd_matches(message: types.Message):
    matches = await db.get_matches(10)
    if not matches:
        await message.answer("История матчей пуста.", reply_markup=main_menu())
        return

    lines = ["⚽ <b>История матчей</b>\n"]
    for m in matches:
        date_str = datetime.strptime(m["date"], "%Y-%m-%d").strftime("%d.%m")
        score = f"{m['our_score']}:{m['their_score']}" if m["our_score"] is not None else "—"
        lines.append(f"  • {date_str} vs {m['opponent']} — {score}")
    await message.answer("\n".join(lines), reply_markup=main_menu())

# ─── POLL ───────────────────────────────────────────────────────────────

@dp.message(Command("poll"))
async def cmd_poll(message: types.Message):
    await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок")
    text = message.text.replace("/poll", "").strip()
    if "|" not in text:
        await message.answer(
            "Использование: /poll Вопрос | Вариант1 | Вариант2 | ...\n"
            "Пример: /poll Куда едем? | Казань | Питер"
        )
        return

    parts = [p.strip() for p in text.split("|")]
    question = parts[0]
    options = parts[1:]
    if len(options) < 2:
        await message.answer("Нужно минимум 2 варианта.")
        return
    if len(options) > 10:
        await message.answer("Максимум 10 вариантов.")
        return

    poll_id = await db.create_poll(question, options, message.chat.id)
    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        kb.button(text=opt, callback_data=f"poll_{poll_id}_{i}")
    kb.adjust(1 if len(options) <= 3 else 2)

    msg = await message.answer(
        f"📊 <b>Опрос</b>\n{question}\n\nВыберите вариант:",
        reply_markup=kb.as_markup(),
    )
    await db.set_poll_msg_id(poll_id, msg.message_id)

@dp.callback_query(lambda c: c.data.startswith("poll_"))
async def poll_callback(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    poll_id = int(parts[1])
    option_index = int(parts[2])
    tg_id = callback.from_user.id
    name = callback.from_user.full_name or "Игрок"
    await ensure_player(tg_id, name)

    await db.vote_poll(poll_id, tg_id, option_index)
    poll = await db.get_poll(poll_id)
    options = poll["options"].split("|||")
    results = await db.get_poll_results(poll_id)

    lines = [f"📊 <b>{poll['question']}</b>\n"]
    for i, opt in enumerate(options):
        count = results.get(i, 0)
        bar = "█" * min(count, 20)
        lines.append(f"  {opt} — {count} {bar}")
    lines.append(f"\nТвой выбор: {options[option_index]}")

    await callback.message.edit_text("\n".join(lines))
    await callback.answer()

# ─── LEAVE ──────────────────────────────────────────────────────────────

@dp.message(Command("leave"))
async def cmd_leave(message: types.Message):
    await db.deactivate_player(message.from_user.id)
    await message.answer("Ты покинул команду. Чтобы вернуться — /start")

# ─── MAIN ───────────────────────────────────────────────────────────────

async def main():
    await db.init()
    await set_bot_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())