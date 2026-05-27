import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from typing import Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, DB_PATH
from db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = Database(DB_PATH)

TEAM_CHAT_ID_KEY = "team_chat_id"
TEAM_NAME_KEY = "team_name"

user_state: dict[int, dict[str, Any]] = {}
birthday_sent: set[tuple[int, str]] = set()


def today_ymd() -> str:
    return date.today().isoformat()


def parse_date(text: str) -> str | None:
    t = text.strip().replace("/", ".")
    parts = t.split(".")
    try:
        if len(parts) == 2:
            d, m = map(int, parts)
            y = date.today().year
            cand = date(y, m, d)
            if cand < date.today():
                cand = date(y + 1, m, d)
            return cand.isoformat()
        if len(parts) == 3:
            d, m, y = map(int, parts)
            if y < 100:
                y += 2000
            return date(y, m, d).isoformat()
    except ValueError:
        return None
    return None


def parse_birth(text: str) -> str | None:
    d = parse_date(text)
    if not d:
        return None
    return d


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚽ Моя статистика"), KeyboardButton(text="🛠 Администрирование")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for text, cb in [
        ("🏐 Тренировка", "a:training"),
        ("⚽ Матч", "a:match"),
        ("📊 Опрос", "a:poll"),
        ("🏷 Название команды", "a:teamname"),
        ("👥 Игроки", "a:players"),
        ("📋 Состав", "a:roster"),
        ("🏆 Рейтинг", "a:top"),
        ("📅 История матчей", "a:matches"),
        ("⬅️ Назад", "nav:home"),
    ]:
        kb.button(text=text, callback_data=cb)
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def stats_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 /stats", callback_data="s:stats")
    kb.button(text="✏️ Переименовать себя", callback_data="s:rename")
    kb.button(text="🎂 Дата рождения", callback_data="s:birth")
    kb.button(text="⬅️ Назад", callback_data="nav:home")
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def back_only() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="nav:home")
    return kb.as_markup()


def match_venue_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Дома", callback_data="m:venue:home")
    kb.button(text="✈️ В гостях", callback_data="m:venue:away")
    kb.button(text="⬅️ Назад", callback_data="nav:admin")
    kb.adjust(2, 1)
    return kb.as_markup()


def score_menu(prefix: str) -> InlineKeyboardMarkup:
    scores = ["0:0", "1:0", "2:0", "1:1", "2:1", "3:1", "3:2", "4:2", "5:3"]
    kb = InlineKeyboardBuilder()
    for s in scores:
        kb.button(text=s, callback_data=f"{prefix}:{s}")
    kb.button(text="✏️ Свой вариант", callback_data=f"{prefix}:custom")
    kb.button(text="⬅️ Назад", callback_data="nav:admin")
    kb.adjust(3, 3, 3, 1, 1)
    return kb.as_markup()


def player_action_menu(pid: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔢 Номер", callback_data=f"p:{pid}:number")
    kb.button(text="🎂 Дата рождения", callback_data=f"p:{pid}:birth")
    kb.button(text="✏️ Переименовать", callback_data=f"p:{pid}:rename")
    kb.button(text="🗑 Удалить", callback_data=f"p:{pid}:delete")
    kb.button(text="⬅️ Назад", callback_data="a:players")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


async def is_admin(chat_id: int, user_id: int) -> bool:
    admins = await bot.get_chat_administrators(chat_id)
    return any(a.user.id == user_id for a in admins)


async def ensure_player(tg_id: int, name: str):
    p = await db.get_player(tg_id)
    if not p:
        await db.register_player(tg_id, name)
        return True
    return False


async def delete_later(message: Message, seconds: int = 15):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


async def answer_ephemeral(message: Message, text: str, markup=None, seconds: int = 15):
    msg = await message.answer(text, reply_markup=markup)
    asyncio.create_task(delete_later(msg, seconds))
    return msg


async def set_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск"),
        BotCommand(command="stats", description="Моя статистика"),
        BotCommand(command="training", description="Тренировка"),
        BotCommand(command="roster", description="Состав"),
        BotCommand(command="top", description="Рейтинг"),
        BotCommand(command="match", description="Матч"),
        BotCommand(command="matches", description="История матчей"),
        BotCommand(command="poll", description="Опрос"),
        BotCommand(command="help", description="Помощь"),
    ], scope=BotCommandScopeDefault())


async def team_name() -> str:
    return await db.get_setting(TEAM_NAME_KEY) or "Футзальная команда"


async def team_chat_id() -> int | None:
    v = await db.get_setting(TEAM_CHAT_ID_KEY)
    return int(v) if v else None


async def ensure_team_chat(chat_id: int):
    if await db.get_setting(TEAM_CHAT_ID_KEY) != str(chat_id):
        await db.set_setting(TEAM_CHAT_ID_KEY, str(chat_id))


async def birthday_watcher():
    while True:
        try:
            chat_id = await team_chat_id()
            if chat_id:
                players = await db.get_players_with_birthdays_today()
                for p in players:
                    key = (p["tg_id"], today_ymd())
                    if key in birthday_sent:
                        continue
                    bd = p["birth_date"]
                    age = date.today().year - int(bd[:4])
                    await bot.send_message(
                        chat_id,
                        f"🎉 <b>Сегодня день рождения у {p['name']}!</b>\nЕму исполнилось {age} лет.",
                    )
                    birthday_sent.add(key)
        except Exception as e:
            logging.warning("birthday watcher: %s", e)
        await asyncio.sleep(1800)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE and await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок"):
        pass
    await message.delete()
    await answer_ephemeral(
        message,
        "👋 <b>Футзал</b>\nВыбери действие.",
        main_menu(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.delete()
    await answer_ephemeral(
        message,
        "📋 /stats /training /roster /top /match /matches /poll",
        main_menu(),
    )


@dp.message(F.text == "⚽ Моя статистика")
async def stats_entry(message: Message):
    await message.delete()
    await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок")
    p = await db.get_player(message.from_user.id)
    if not p or not p["birth_date"]:
        await db.update_player_name(message.from_user.id, message.from_user.full_name or "Игрок")
        user_state[message.from_user.id] = {"mode": "first_reg_birth"}
        await answer_ephemeral(message, "Введи дату рождения в формате ДД.ММ.ГГГГ", back_only())
        return
    await stats_show(message)


@dp.message(F.text == "🛠 Администрирование")
async def admin_entry(message: Message):
    await message.delete()
    if message.chat.type == ChatType.PRIVATE or await is_admin(message.chat.id, message.from_user.id):
        await answer_ephemeral(message, "🛠 <b>Администрирование</b>", admin_menu())
    else:
        await answer_ephemeral(message, "Нет доступа.", main_menu(), 8)


async def stats_show(message: Message):
    p = await db.get_player(message.from_user.id)
    name = p["name"] if p else message.from_user.full_name
    stats = await db.get_player_stats(message.from_user.id)
    if not stats or stats["total"] == 0:
        txt = f"📊 <b>{name}</b>\nПока нет статистики."
    else:
        pct = round((stats["yes"] or 0) / stats["total"] * 100) if stats["total"] else 0
        txt = (
            f"📊 <b>{name}</b>\n"
            f"✅ Был: {stats['yes'] or 0} ({pct}%)\n"
            f"❌ Не был: {stats['no'] or 0}\n"
            f"🤔 Под вопросом: {stats['maybe'] or 0}"
        )
    await answer_ephemeral(message, txt, stats_menu())


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    await message.delete()
    await ensure_player(message.from_user.id, message.from_user.full_name or "Игрок")
    await stats_show(message)


@dp.message(Command("training"))
async def cmd_training(message: Message):
    await message.delete()
    if message.chat.type != ChatType.PRIVATE:
        await ensure_team_chat(message.chat.id)
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await answer_ephemeral(message, "Используй: /training ДД.ММ [ЧЧ:ММ] [место]", admin_menu())
        return
    d = parse_date(args[1])
    if not d:
        await answer_ephemeral(message, "Неверная дата.", admin_menu())
        return
    time_ = args[2] if len(args) > 2 else "20:00"
    training_id = await db.create_training(d, time_, "Обычное место")
    t = await db.get_training(training_id)
    await message.answer(f"🏐 <b>Тренировка</b>\n{datetime.fromisoformat(t['date']).strftime('%d.%m')} {t['time']}", reply_markup=training_keyboard(training_id))


def training_keyboard(training_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Буду", callback_data=f"att:yes:{training_id}")
    kb.button(text="❌ Не буду", callback_data=f"att:no:{training_id}")
    kb.button(text="🤔 Под вопросом", callback_data=f"att:maybe:{training_id}")
    kb.adjust(3)
    return kb.as_markup()


@dp.message(Command("roster"))
async def cmd_roster(message: Message):
    await message.delete()
    players = await db.get_all_players()
    text = "👥 <b>Состав</b>\n" + ("\n".join([f"• {p['name']}" + (f" №{p['player_number']}" if p['player_number'] else "") for p in players]) or "Пусто")
    await answer_ephemeral(message, text, admin_menu() if (message.chat.type == ChatType.PRIVATE or await is_admin(message.chat.id, message.from_user.id)) else main_menu())


@dp.message(Command("top"))
async def cmd_top(message: Message):
    await message.delete()
    top = await db.get_top_attendance(10)
    text = "🏆 <b>Рейтинг</b>\n" + "\n".join([f"{i+1}. {p['name']} — {p['yes'] or 0}" for i, p in enumerate(top)])
    await answer_ephemeral(message, text, admin_menu() if message.chat.type == ChatType.PRIVATE or await is_admin(message.chat.id, message.from_user.id) else main_menu())


@dp.message(Command("matches"))
async def cmd_matches(message: Message):
    await message.delete()
    rows = await db.get_matches(10)
    text = "📋 <b>Матчи</b>\n" + "\n".join([f"• {r['date']} vs {r['opponent']} — {r['our_score'] if r['our_score'] is not None else '—'}:{r['their_score'] if r['their_score'] is not None else '—'}" for r in rows])
    await answer_ephemeral(message, text, admin_menu())


@dp.message(Command("match"))
async def cmd_match(message: Message):
    await message.delete()
    if message.chat.type != ChatType.PRIVATE and not await is_admin(message.chat.id, message.from_user.id):
        return
    user_state[message.from_user.id] = {"mode": "match", "data": {}}
    await message.answer("⚽ <b>Добавление матча</b>", reply_markup=match_venue_menu())


@dp.message(Command("poll"))
async def cmd_poll(message: Message):
    await message.delete()
    if message.chat.type != ChatType.PRIVATE and not await is_admin(message.chat.id, message.from_user.id):
        return
    await answer_ephemeral(message, "Формат: /poll Вопрос | Вариант1 | Вариант2", admin_menu())


@dp.message(F.text)
async def free_text(message: Message):
    st = user_state.get(message.from_user.id)
    if not st:
        return
    if st.get("mode") == "first_reg_birth":
        bd = parse_birth(message.text)
        if not bd:
            await message.delete()
            await answer_ephemeral(message, "Неверная дата. Формат ДД.ММ.ГГГГ", stats_menu())
            return
        await db.update_player_birth(message.from_user.id, bd)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Сохранено.", main_menu(), 8)
    elif st.get("mode") == "rename_self":
        await db.update_player_name(message.from_user.id, message.text.strip())
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Имя обновлено.", stats_menu(), 8)
    elif st.get("mode") == "match":
        data = st.setdefault("data", {})
        step = st.get("step")
        txt = message.text.strip()
        if step == "score_custom":
            data["score"] = txt
            st["step"] = "ht_custom"
            await message.delete()
            await answer_ephemeral(message, "Счёт 1-го тайма: отправь как 1:0", back_only())
        elif step == "ht_custom":
            data["ht_score"] = txt
            st["step"] = "date"
            await message.delete()
            await answer_ephemeral(message, "Дата матча: ДД.ММ", back_only())
        elif step == "opponent":
            data["opponent"] = txt
            d = parse_date(data.get("date", ""))
            if not d:
                await message.delete()
                await answer_ephemeral(message, "Неверная дата.", back_only())
                return
            our, their = map(int, data["score"].split(":"))
            hot, htheir = map(int, data["ht_score"].split(":"))
            mid = await db.create_match(d, data["opponent"], data.get("venue", "home"))
            await db.update_match_score(mid, our, their, hot, htheir)
            user_state.pop(message.from_user.id, None)
            await message.delete()
            await answer_ephemeral(message, f"Матч сохранён: {data['opponent']} {data['score']}", admin_menu(), 10)
        elif step == "date":
            data["date"] = txt
            st["step"] = "opponent"
            await message.delete()
            await answer_ephemeral(message, "Соперник?", back_only())
    elif st.get("mode") == "admin_rename_player":
        pid = st["pid"]
        await db.update_player_name(pid, message.text.strip())
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Игрок переименован.", admin_menu(), 10)
    elif st.get("mode") == "admin_birth_player":
        pid = st["pid"]
        bd = parse_birth(message.text)
        if not bd:
            await message.delete()
            await answer_ephemeral(message, "Неверная дата.", admin_menu(), 8)
            return
        await db.update_player_birth(pid, bd)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Дата рождения сохранена.", admin_menu(), 10)
    elif st.get("mode") == "admin_number_player":
        pid = st["pid"]
        try:
            n = int(message.text.strip())
        except ValueError:
            await message.delete()
            await answer_ephemeral(message, "Нужен номер числом.", admin_menu(), 8)
            return
        await db.update_player_number(pid, n)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Номер сохранён.", admin_menu(), 10)
    elif st.get("mode") == "admin_teamname":
        await db.set_setting(TEAM_NAME_KEY, message.text.strip())
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Название команды сохранено.", admin_menu(), 10)


@dp.callback_query(F.data == "nav:home")
async def nav_home(cb: CallbackQuery):
    await cb.answer()
    await cb.message.edit_text("Выбери действие", reply_markup=None)
    await cb.message.answer("Главное меню", reply_markup=main_menu())


@dp.callback_query(F.data == "nav:admin")
async def nav_admin(cb: CallbackQuery):
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text("🛠 Администрирование", reply_markup=admin_menu())


@dp.callback_query(F.data == "s:stats")
async def stats_cb(cb: CallbackQuery):
    await cb.answer()
    await cb.message.delete()
    await stats_show(cb.message)


@dp.callback_query(F.data == "s:rename")
async def rename_self(cb: CallbackQuery):
    await cb.answer()
    user_state[cb.from_user.id] = {"mode": "rename_self"}
    await cb.message.answer("Введите новое имя.", reply_markup=stats_menu())


@dp.callback_query(F.data == "s:birth")
async def birth_self(cb: CallbackQuery):
    await cb.answer()
    user_state[cb.from_user.id] = {"mode": "first_reg_birth"}
    await cb.message.answer("Введите дату рождения: ДД.ММ.ГГГГ", reply_markup=stats_menu())


@dp.callback_query(F.data == "a:teamname")
async def admin_teamname(cb: CallbackQuery):
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    user_state[cb.from_user.id] = {"mode": "admin_teamname"}
    await cb.message.answer("Введите название команды.", reply_markup=admin_menu())


@dp.callback_query(F.data == "a:players")
async def admin_players(cb: CallbackQuery):
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    players = await db.get_all_players()
    kb = InlineKeyboardBuilder()
    for p in players:
        kb.button(text=p["name"], callback_data=f"p:{p['tg_id']}:menu")
    kb.button(text="⬅️ Назад", callback_data="nav:admin")
    kb.adjust(2)
    await cb.message.edit_text("👥 Игроки", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("p:"))
async def player_actions(cb: CallbackQuery):
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    _, pid, action = cb.data.split(":")
    pid = int(pid)
    if action == "menu":
        p = await db.get_player(pid)
        text = f"<b>{p['name']}</b>"
        if p["player_number"]:
            text += f"\n№ {p['player_number']}"
        if p["birth_date"]:
            text += f"\n🎂 {p['birth_date']}"
        await cb.answer()
        await cb.message.edit_text(text, reply_markup=player_action_menu(pid))
        return
    if action == "rename":
        user_state[cb.from_user.id] = {"mode": "admin_rename_player", "pid": pid}
        await cb.answer()
        await cb.message.answer("Новое имя игрока?")
    elif action == "birth":
        user_state[cb.from_user.id] = {"mode": "admin_birth_player", "pid": pid}
        await cb.answer()
        await cb.message.answer("Дата рождения: ДД.ММ.ГГГГ")
    elif action == "number":
        user_state[cb.from_user.id] = {"mode": "admin_number_player", "pid": pid}
        await cb.answer()
        await cb.message.answer("Номер игрока?")
    elif action == "delete":
        await db.deactivate_player(pid)
        await cb.answer("Удалено", show_alert=False)
        await admin_players(cb)


@dp.callback_query(F.data == "a:roster")
async def admin_roster(cb: CallbackQuery):
    await cb.answer()
    players = await db.get_all_players()
    lines = [f"👥 <b>{await team_name()}</b>"]
    for p in players:
        line = p["name"]
        if p["player_number"]:
            line += f" №{p['player_number']}"
        lines.append(f"• {line}")
    await cb.message.edit_text("\n".join(lines), reply_markup=admin_menu())


@dp.callback_query(F.data == "a:top")
async def admin_top(cb: CallbackQuery):
    await cb.answer()
    rows = await db.get_top_attendance(10)
    txt = "🏆 Рейтинг\n" + "\n".join([f"{i+1}. {r['name']} — {r['yes'] or 0}" for i, r in enumerate(rows)])
    await cb.message.edit_text(txt or "Пусто", reply_markup=admin_menu())


@dp.callback_query(F.data == "a:matches")
async def admin_matches(cb: CallbackQuery):
    await cb.answer()
    rows = await db.get_matches(10)
    txt = "📅 Матчи\n" + "\n".join([f"• {r['date']} vs {r['opponent']}" for r in rows])
    await cb.message.edit_text(txt or "Пусто", reply_markup=admin_menu())


@dp.callback_query(F.data == "a:training")
async def admin_training(cb: CallbackQuery):
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    user_state[cb.from_user.id] = {"mode": "admin_training"}
    await cb.message.answer("Напиши: /training ДД.ММ ЧЧ:ММ", reply_markup=admin_menu())


@dp.callback_query(F.data == "a:match")
async def admin_match(cb: CallbackQuery):
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    user_state[cb.from_user.id] = {"mode": "match", "step": "venue", "data": {}}
    await cb.message.answer("Где играем?", reply_markup=match_venue_menu())


@dp.callback_query(F.data.startswith("m:venue:"))
async def match_venue(cb: CallbackQuery):
    _, _, venue = cb.data.split(":")
    st = user_state.setdefault(cb.from_user.id, {"mode": "match", "data": {}})
    st["data"]["venue"] = venue
    st["step"] = "score"
    await cb.answer()
    await cb.message.answer("Выбери счёт", reply_markup=score_menu("m:score"))


@dp.callback_query(F.data.startswith("m:score:"))
async def match_score(cb: CallbackQuery):
    score = cb.data.split(":", 2)[2]
    st = user_state.setdefault(cb.from_user.id, {"mode": "match", "data": {}})
    if score == "custom":
        st["step"] = "score_custom"
        await cb.answer()
        await cb.message.answer("Свой счёт? Формат 3:2")
        return
    st["data"]["score"] = score
    st["step"] = "ht"
    await cb.answer()
    await cb.message.answer("Счёт после 1 тайма", reply_markup=score_menu("m:ht"))


@dp.callback_query(F.data.startswith("m:ht:"))
async def match_ht(cb: CallbackQuery):
    score = cb.data.split(":", 2)[2]
    st = user_state.setdefault(cb.from_user.id, {"mode": "match", "data": {}})
    if score == "custom":
        st["step"] = "ht_custom"
        await cb.answer()
        await cb.message.answer("Свой счёт 1 тайма? Формат 1:0")
        return
    st["data"]["ht_score"] = score
    st["step"] = "date"
    await cb.answer()
    await cb.message.answer("Дата матча: ДД.ММ")


@dp.message(Command("matchconfirm"))
async def match_confirm(message: Message):
    pass


@dp.message(F.text)
async def router_text(message: Message):
    st = user_state.get(message.from_user.id)
    if not st:
        return
    mode = st.get("mode")
    text = message.text.strip()
    if mode == "first_reg_birth":
        bd = parse_birth(text)
        if not bd:
            await message.delete()
            await answer_ephemeral(message, "Дата должна быть ДД.ММ.ГГГГ", main_menu())
            return
        await db.update_player_birth(message.from_user.id, bd)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Сохранено.", main_menu(), 8)
    elif mode == "rename_self":
        await db.update_player_name(message.from_user.id, text)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Имя обновлено.", stats_menu(), 8)
    elif mode == "admin_teamname":
        await db.set_setting(TEAM_NAME_KEY, text)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Готово.", admin_menu(), 8)
    elif mode == "admin_rename_player":
        await db.update_player_name(st["pid"], text)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Игрок переименован.", admin_menu(), 8)
    elif mode == "admin_birth_player":
        bd = parse_birth(text)
        if not bd:
            await message.delete()
            await answer_ephemeral(message, "Дата должна быть ДД.ММ.ГГГГ", admin_menu())
            return
        await db.update_player_birth(st["pid"], bd)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Сохранено.", admin_menu(), 8)
    elif mode == "admin_number_player":
        try:
            num = int(text)
        except ValueError:
            await message.delete()
            await answer_ephemeral(message, "Нужен номер числом.", admin_menu())
            return
        await db.update_player_number(st["pid"], num)
        user_state.pop(message.from_user.id, None)
        await message.delete()
        await answer_ephemeral(message, "Сохранено.", admin_menu(), 8)
    elif mode == "match":
        data = st.setdefault("data", {})
        if st.get("step") == "score_custom":
            if ":" not in text:
                await message.delete()
                await answer_ephemeral(message, "Формат 3:2", admin_menu())
                return
            data["score"] = text
            st["step"] = "ht_custom"
            await message.delete()
            await answer_ephemeral(message, "Счёт 1 тайма?", admin_menu())
        elif st.get("step") == "ht_custom":
            if ":" not in text:
                await message.delete()
                await answer_ephemeral(message, "Формат 1:0", admin_menu())
                return
            data["ht_score"] = text
            st["step"] = "date"
            await message.delete()
            await answer_ephemeral(message, "Дата матча: ДД.ММ", admin_menu())
        elif st.get("step") == "date":
            if not parse_date(text):
                await message.delete()
                await answer_ephemeral(message, "Неверная дата.", admin_menu())
                return
            data["date"] = text
            st["step"] = "opponent"
            await message.delete()
            await answer_ephemeral(message, "Соперник?", admin_menu())
        elif st.get("step") == "opponent":
            data["opponent"] = text
            date_iso = parse_date(data["date"])
            our, their = map(int, data["score"].split(":"))
            our_ht, their_ht = map(int, data["ht_score"].split(":"))
            mid = await db.create_match(date_iso, data["opponent"], data.get("venue", "home"))
            await db.update_match_score(mid, our, their, our_ht, their_ht)
            user_state.pop(message.from_user.id, None)
            await message.delete()
            await answer_ephemeral(message, "Матч сохранён.", admin_menu(), 8)


@dp.message()
async def delete_service_messages(message: Message):
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP} and message.text and message.text.startswith("/"):
        try:
            await message.delete()
        except Exception:
            pass


async def main():
    await db.init()
    await set_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(birthday_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())