import asyncio
import logging
from datetime import date, datetime
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, DB_PATH
from db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = Database(DB_PATH)

# Ключи для хранения настроек команды в таблице settings
TEAM_CHAT_ID_KEY = "***"
TEAM_NAME_KEY = "***"

# Состояния пользователей: {tg_id: {"mode": "...", ...}}
user_state: dict[int, dict[str, Any]] = {}
# Множество отправленных поздравлений с ДР: {(tg_id, дата)}
birthday_sent: set[tuple[int, str]] = set()


def today_ymd():
    """Сегодняшняя дата в ISO-формате (ГГГГ-ММ-ДД)."""
    return date.today().isoformat()


def parse_date(text: str) -> str | None:
    """
    Преобразует текстовую дату в ISO-формат.
    Поддерживает ДД.ММ, ДД/ММ, ДД.ММ.ГГГГ, ДД.ММ.ГГ (YY -> 20YY).
    """
    t = text.strip().replace("/", ".")
    parts = t.split(".")
    try:
        if len(parts) == 2:
            # Только день и месяц — добавляем текущий год (или следующий, если дата уже прошла)
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


# ═══════════════════════════ КЛАВИАТУРЫ ═══════════════════════════


def main_menu() -> ReplyKeyboardMarkup:
    """
    Стартовая reply-клавиатура с двумя кнопками.
    Исчезает после первого нажатия (one_time_keyboard).
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚽ Моя статистика"),
             KeyboardButton(text="🛠 Администрирование")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def admin_menu() -> InlineKeyboardMarkup:
    """Меню администратора со списком доступных действий."""
    kb = InlineKeyboardBuilder()
    for text, cb in [
        ("🏐 Тренировка", "ad:training"),
        ("⚽ Матч", "ad:match"),
        ("📊 Опрос", "ad:poll"),
        ("🏷 Название команды", "ad:teamname"),
        ("👥 Игроки", "ad:players"),
        ("📋 Состав", "ad:roster"),
        ("🏆 Рейтинг", "ad:top"),
        ("📅 История матчей", "ad:matches"),
        ("⬅️ На главную", "nav:home"),
    ]:
        kb.button(text=text, callback_data=cb)
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def stats_menu() -> InlineKeyboardMarkup:
    """Меню статистики игрока."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="st:show")
    kb.button(text="✏️ Сменить имя", callback_data="st:rename")
    kb.button(text="🎂 Дата рождения", callback_data="st:birth")
    kb.button(text="⬅️ На главную", callback_data="nav:home")
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def training_kb(tid: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для отметки на тренировку.
    Кнопки: ✅ Буду / ❌ Не буду / 🤔 Под вопросом.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Буду", callback_data=f"att:yes:{tid}")
    kb.button(text="❌ Не буду", callback_data=f"att:no:{tid}")
    kb.button(text="🤔 Под вопросом", callback_data=f"att:maybe:{tid}")
    kb.adjust(3)
    return kb.as_markup()


def match_venue_kb() -> InlineKeyboardMarkup:
    """Выбор места проведения матча."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Дома", callback_data="mv:home")
    kb.button(text="✈️ В гостях", callback_data="mv:away")
    kb.button(text="⬅️ Назад", callback_data="nav:admin")
    kb.adjust(2, 1)
    return kb.as_markup()


def score_kb(prefix: str) -> InlineKeyboardMarkup:
    """Выбор счёта из готовых вариантов или свободный ввод."""
    scores = ["0:0", "1:0", "2:0", "1:1", "2:1", "3:1", "3:2", "4:2", "5:3"]
    kb = InlineKeyboardBuilder()
    for s in scores:
        kb.button(text=s, callback_data=f"{prefix}:{s}")
    kb.button(text="✏️ Свой", callback_data=f"{prefix}:custom")
    kb.button(text="⬅️ Назад", callback_data="nav:admin")
    kb.adjust(3, 3, 3, 1, 1)
    return kb.as_markup()


def player_menu(pid: int) -> InlineKeyboardMarkup:
    """Меню управления конкретным игроком (для админа)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔢 Номер", callback_data=f"pl:{pid}:num")
    kb.button(text="🎂 ДР", callback_data=f"pl:{pid}:birth")
    kb.button(text="✏️ Переименовать", callback_data=f"pl:{pid}:rename")
    kb.button(text="🗑 Удалить", callback_data=f"pl:{pid}:delete")
    kb.button(text="⬅️ Назад к списку", callback_data="ad:players")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


# ═══════════════════════════ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ═══════════════════════════


async def is_admin(chat_id: int, user_id: int) -> bool:
    """Проверка, является ли пользователь администратором чата."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        return any(a.user.id == user_id for a in admins)
    except Exception:
        return False


async def ensure_player(tg_id: int, name: str):
    """Регистрирует игрока, если его ещё нет в БД."""
    p = await db.get_player(tg_id)
    if not p:
        await db.register_player(tg_id, name)


async def del_later(msg: Message, sec: int = 15):
    """Удаляет сообщение через указанное количество секунд."""
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except Exception:
        pass


async def ephemeral(msg: Message, text: str, sec: int = 15, reply_markup=None):
    """
    Отправляет временное сообщение, которое удалится через sec секунд.
    Используется для подсказок, статусов и т.п.
    """
    m = await msg.answer(text, reply_markup=reply_markup)
    asyncio.create_task(del_later(m, sec))
    return m


async def set_commands():
    """Устанавливает список команд для автодополнения в Telegram."""
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
    """Возвращает название команды из настроек."""
    return await db.get_setting(TEAM_NAME_KEY) or "Футзальная команда"


async def team_chat_id() -> int | None:
    """Возвращает ID чата команды из настроек."""
    v = await db.get_setting(TEAM_CHAT_ID_KEY)
    return int(v) if v else None


async def ensure_team_chat(chat_id: int):
    """Сохраняет ID чата как командный, если ещё не сохранён."""
    if await db.get_setting(TEAM_CHAT_ID_KEY) != str(chat_id):
        await db.set_setting(TEAM_CHAT_ID_KEY, str(chat_id))


async def show_stats(msg: Message, user_id: int):
    """Показывает статистику игрока в persistent-сообщении с инлайн-меню."""
    p = await db.get_player(user_id)
    name = p["name"] if p else "Игрок"
    stats = await db.get_player_stats(user_id)
    if not stats or not stats["total"]:
        txt = f"📊 <b>{name}</b>\nПока нет статистики."
    else:
        pct = round(stats["yes"] / stats["total"] * 100)
        txt = (
            f"📊 <b>{name}</b>\n"
            f"✅ Был: {stats['yes']} ({pct}%)\n"
            f"❌ Не был: {stats['no']}\n"
            f"🤔 Под вопросом: {stats['maybe']}"
        )
    try:
        await msg.delete()
    except Exception:
        pass
    await msg.answer(txt, reply_markup=stats_menu())


async def show_admin_menu(msg: Message):
    """Показывает меню администратора (persistent-сообщение)."""
    try:
        await msg.delete()
    except Exception:
        pass
    await msg.answer("🛠 <b>Администрирование</b>", reply_markup=admin_menu())


async def build_training_text(tid: int) -> str:
    """
    Формирует текст сообщения о тренировке со сгруппированным списком участников.
    Пример:
      🏐 Тренировка
      📅 27.05 | 20:00

      ✅ Буду (2):
      · Игрок 1
      · Игрок 2

      🤔 Под вопросом (1):
      · Игрок 3

      ❌ Не буду (1):
      · Игрок 4
    """
    tr = await db.get_training(tid)
    if not tr:
        return "Тренировка не найдена."
    dt = date.fromisoformat(tr["date"]).strftime("%d.%m.%Y")
    parts = [f"🏐 <b>Тренировка</b>\n📅 {dt} | {tr['time']}\n"]

    # Получаем список отметившихся
    attendances = await db.get_training_attendance(tid)
    grouped = {"yes": [], "no": [], "maybe": []}
    for a in attendances:
        player = await db.get_player(a["player_id"])
        name = player["name"] if player else f"id{a['player_id']}"
        grouped[a["status"]].append(name)

    labels = {"yes": "✅ Буду", "no": "❌ Не буду", "maybe": "🤔 Под вопросом"}
    for status in ("yes", "maybe", "no"):
        names = grouped[status]
        if names:
            parts.append(f"{labels[status]} ({len(names)}):")
            for n in names:
                parts.append(f"· {n}")
            parts.append("")

    return "\n".join(parts)


# ═══════════════════════════ ДНИ РОЖДЕНИЯ ═══════════════════════════


async def birthday_watcher():
    """Фоновая задача: проверяет дни рождения каждые 30 минут."""
    while True:
        try:
            chat_id = await team_chat_id()
            if chat_id:
                for p in await db.get_players_with_birthdays_today():
                    key = (p["tg_id"], today_ymd())
                    if key in birthday_sent:
                        continue
                    age = date.today().year - int(p["birth_date"][:4])
                    await bot.send_message(
                        chat_id,
                        f"🎉 <b>Сегодня день рождения у {p['name']}!</b>\nЕму исполнилось {age} лет.",
                    )
                    birthday_sent.add(key)
        except Exception as e:
            logging.warning("birthday: %s", e)
        await asyncio.sleep(1800)


# ═══════════════════════════ НОВЫЕ УЧАСТНИКИ ═══════════════════════════


@dp.chat_member()
async def on_new_member(event: ChatMemberUpdated):
    """
    Приветствует нового участника чата.
    Срабатывает при добавлении пользователя в группу.
    """
    # Проверяем, что это добавление нового человека (а не бота и не выход)
    if event.new_chat_member.status in ("member", "administrator") and \
       event.old_chat_member.status in ("left", "kicked", "restricted") and \
       not event.new_chat_member.user.is_bot:
        uid = event.new_chat_member.user.id
        name = event.new_chat_member.user.full_name or "Игрок"
        await ensure_player(uid, name)
        tname = await team_name()
        await event.answer(
            f"👋 <b>{name}</b>, мы рады приветствовать тебя в команде <b>{tname}</b>! 🎉\n"
            f"Напиши /start чтобы увидеть меню."
        )


# ═══════════════════════════ ОБРАБОТЧИКИ КОМАНД ═══════════════════════════


@dp.message(Command("start"))
async def cmd_start(msg: Message):
    """Команда /start — показывает reply-клавиатуру с главным меню."""
    if msg.chat.type != ChatType.PRIVATE:
        await ensure_team_chat(msg.chat.id)
    user = msg.from_user
    await ensure_player(user.id, user.full_name or "Игрок")
    try:
        await msg.delete()
    except Exception:
        pass
    await ephemeral(
        msg,
        "👋 <b>Футзальная команда</b>\nИспользуй кнопки меню ниже ⬇️",
        sec=25,
        reply_markup=main_menu(),
    )


@dp.message(Command("help"))
async def cmd_help(msg: Message):
    """Команда /help — список доступных команд."""
    try:
        await msg.delete()
    except Exception:
        pass
    await ephemeral(msg, "Команды: /stats /training /roster /top /match /matches /poll")


@dp.message(F.text == "⚽ Моя статистика")
async def stats_btn(msg: Message):
    """Обработчик нажатия reply-кнопки «Моя статистика»."""
    await ensure_player(msg.from_user.id, msg.from_user.full_name or "Игрок")
    try:
        await msg.delete()
    except Exception:
        pass
    p = await db.get_player(msg.from_user.id)
    if not p or not p["birth_date"]:
        # Первый вход — запрашиваем дату рождения
        await db.update_player_name(msg.from_user.id, msg.from_user.full_name or "Игрок")
        user_state[msg.from_user.id] = {"mode": "birth_reg"}
        await ephemeral(msg, "Введи дату рождения в формате ДД.ММ.ГГГГ", sec=30)
        return
    await show_stats(msg, msg.from_user.id)


@dp.message(F.text == "🛠 Администрирование")
async def admin_btn(msg: Message):
    """Обработчик нажатия reply-кнопки «Администрирование»."""
    try:
        await msg.delete()
    except Exception:
        pass
    if msg.chat.type == ChatType.PRIVATE or await is_admin(msg.chat.id, msg.from_user.id):
        await show_admin_menu(msg)
    else:
        await ephemeral(msg, "❌ Нет доступа. Эта кнопка только для администраторов чата.", sec=8)


@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    """Команда /stats — показывает статистику игрока."""
    await ensure_player(msg.from_user.id, msg.from_user.full_name or "Игрок")
    try:
        await msg.delete()
    except Exception:
        pass
    await show_stats(msg, msg.from_user.id)


@dp.message(Command("training"))
async def cmd_training(msg: Message):
    """
    Команда /training — создаёт тренировку.
    Формат: /training ДД.ММ [ЧЧ:ММ] [место]
    """
    if msg.chat.type != ChatType.PRIVATE:
        await ensure_team_chat(msg.chat.id)
    try:
        await msg.delete()
    except Exception:
        pass
    args = msg.text.split(maxsplit=2)
    if len(args) < 2:
        await ephemeral(msg, "Формат: /training ДД.ММ [ЧЧ:ММ] [место]")
        return
    d = parse_date(args[1])
    if not d:
        await ephemeral(msg, "Неверная дата.")
        return
    t = args[2] if len(args) > 2 else "20:00"
    tid = await db.create_training(d, t)
    text = await build_training_text(tid)
    await msg.answer(text, reply_markup=training_kb(tid))


@dp.message(Command("roster"))
async def cmd_roster(msg: Message):
    """Команда /roster — показывает состав команды."""
    try:
        await msg.delete()
    except Exception:
        pass
    players = await db.get_all_players()
    lines = [f"👥 <b>{await team_name()}</b>"]
    for p in players:
        n = p["name"] + (f" №{p['player_number']}" if p["player_number"] else "")
        lines.append(f"· {n}")
    await ephemeral(msg, "\n".join(lines) or "Пока пусто.", sec=30)


@dp.message(Command("top"))
async def cmd_top(msg: Message):
    """Команда /top — рейтинг посещаемости тренировок."""
    try:
        await msg.delete()
    except Exception:
        pass
    top = await db.get_top_attendance(10)
    lines = ["🏆 <b>Рейтинг посещаемости тренировок</b>"]
    for i, p in enumerate(top, 1):
        lines.append(f"{i}. {p['name']} — {p['yes'] or 0}")
    await ephemeral(msg, "\n".join(lines) or "Пока пусто.", sec=30)


@dp.message(Command("matches"))
async def cmd_matches(msg: Message):
    """Команда /matches — история матчей."""
    try:
        await msg.delete()
    except Exception:
        pass
    rows = await db.get_matches(10)
    lines = ["📋 <b>История матчей</b>"]
    for r in rows:
        s = f"{r['our_score'] or '—'}:{r['their_score'] or '—'}"
        dm = date.fromisoformat(r['date']).strftime('%d.%m.%Y')
        lines.append(f"· {dm} vs {r['opponent']} — {s}")
    await ephemeral(msg, "\n".join(lines) or "Пока пусто.", sec=30)


@dp.message(Command("match"))
async def cmd_match(msg: Message):
    """Команда /match — создание матча (пошаговый мастер)."""
    try:
        await msg.delete()
    except Exception:
        pass
    if msg.chat.type != ChatType.PRIVATE and not await is_admin(msg.chat.id, msg.from_user.id):
        return
    user_state[msg.from_user.id] = {"mode": "match", "data": {}}
    await msg.answer("⚽ Где играем?", reply_markup=match_venue_kb())


@dp.message(Command("poll"))
async def cmd_poll(msg: Message):
    """Команда /poll — создание опроса. Формат: /poll Вопрос | Вариант1 | Вариант2"""
    try:
        await msg.delete()
    except Exception:
        pass
    if msg.chat.type != ChatType.PRIVATE and not await is_admin(msg.chat.id, msg.from_user.id):
        return
    text = msg.text.replace("/poll", "").strip()
    if "|" not in text:
        await ephemeral(msg, "Формат: /poll Вопрос | Вариант1 | Вариант2")
        return
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        await ephemeral(msg, "Нужен вопрос и минимум 2 варианта.")
        return
    question = parts[0]
    options = parts[1:]
    pid = await db.create_poll(question, options, msg.chat.id)
    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        kb.button(text=opt, callback_data=f"pv:{pid}:{i}")
    kb.adjust(1 if len(options) <= 3 else 2)
    m = await msg.answer(f"📊 <b>Опрос</b>\n{question}", reply_markup=kb.as_markup())
    await db.set_poll_msg_id(pid, m.message_id)


# ═══════════════════════════ ОБРАБОТЧИКИ CALLBACK-ЗАПРОСОВ ═══════════════════════════


@dp.callback_query(F.data == "nav:home")
async def nav_home(cb: CallbackQuery):
    """Возвращает пользователя к reply-клавиатуре главного меню."""
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await ephemeral(cb.message, "Главное меню", sec=4, reply_markup=main_menu())


@dp.callback_query(F.data == "nav:admin")
async def nav_admin(cb: CallbackQuery):
    """Возвращает в меню администратора."""
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("❌ Нет доступа", show_alert=True)
        return
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer("🛠 <b>Администрирование</b>", reply_markup=admin_menu())


@dp.callback_query(F.data.startswith("att:"))
async def att_cb(cb: CallbackQuery):
    """
    Обрабатывает нажатие кнопки отметки на тренировку.
    Обновляет текст сообщения — показывает сгруппированный список участников.
    """
    _, status, tid = cb.data.split(":")
    tid = int(tid)
    uid = cb.from_user.id
    name = cb.from_user.full_name or "Игрок"
    await ensure_player(uid, name)
    await db.set_attendance(tid, uid, status)

    labels = {"yes": "✅ Буду", "no": "❌ Не буду", "maybe": "🤔 Под вопросом"}
    await cb.answer(f"{labels[status]}", show_alert=False)

    # Пересобираем текст с группами участников и обновляем сообщение
    text = await build_training_text(tid)
    try:
        await cb.message.edit_text(text, reply_markup=training_kb(tid))
    except Exception:
        pass


@dp.callback_query(F.data == "st:show")
async def st_show(cb: CallbackQuery):
    """Показывает статистику игрока (из инлайн-меню статистики)."""
    await cb.answer()
    await ensure_player(cb.from_user.id, cb.from_user.full_name or "Игрок")
    await show_stats(cb.message, cb.from_user.id)


@dp.callback_query(F.data == "st:rename")
async def st_rename(cb: CallbackQuery):
    """Запрашивает новое имя для смены (из инлайн-меню статистики)."""
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    user_state[cb.from_user.id] = {"mode": "rename_self"}
    await cb.message.answer("✏️ Введи новое имя:")


@dp.callback_query(F.data == "st:birth")
async def st_birth(cb: CallbackQuery):
    """Запрашивает дату рождения (из инлайн-меню статистики)."""
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    user_state[cb.from_user.id] = {"mode": "birth_reg"}
    await cb.message.answer("🎂 Введи дату рождения в формате ДД.ММ.ГГГГ:")


@dp.callback_query(F.data.startswith("ad:"))
async def admin_cb(cb: CallbackQuery):
    """Обрабатывает нажатия кнопок в меню администратора."""
    action = cb.data.split(":")[1]
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("❌ Нет доступа", show_alert=True)
        return
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass

    if action == "training":
        await ephemeral(cb.message, "Формат: /training ДД.ММ [ЧЧ:ММ]")
    elif action == "poll":
        await ephemeral(cb.message, "Формат: /poll Вопрос | Вариант1 | Вариант2")
    elif action == "teamname":
        user_state[cb.from_user.id] = {"mode": "set_teamname"}
        await ephemeral(cb.message, "🏷 Введи название команды:", sec=30)
    elif action == "players":
        players = await db.get_all_players()
        if not players:
            await cb.message.answer("👥 Игроков пока нет.")
            return
        kb = InlineKeyboardBuilder()
        for p in players:
            kb.button(text=p["name"], callback_data=f"pl:{p['tg_id']}:menu")
        kb.button(text="⬅️ Назад", callback_data="nav:admin")
        kb.adjust(2)
        await cb.message.answer("👥 <b>Игроки</b>", reply_markup=kb.as_markup())
    elif action == "roster":
        players = await db.get_all_players()
        lines = [f"👥 <b>{await team_name()}</b>"]
        for p in players:
            n = p["name"] + (f" №{p['player_number']}" if p["player_number"] else "")
            lines.append(f"· {n}")
        await cb.message.answer("\n".join(lines), reply_markup=admin_menu())
    elif action == "top":
        top = await db.get_top_attendance(10)
        lines = ["🏆 <b>Рейтинг посещаемости тренировок</b>"]
        for i, p in enumerate(top, 1):
            lines.append(f"{i}. {p['name']} — {p['yes'] or 0} ✅")
        await cb.message.answer("\n".join(lines), reply_markup=admin_menu())
    elif action == "matches":
        rows = await db.get_matches(10)
        lines = ["📅 <b>История матчей</b>"]
        for r in rows:
            s = f"{r['our_score'] or '—'}:{r['their_score'] or '—'}"
            lines.append(f"· {dm} vs {r['opponent']} — {s}")
        await cb.message.answer("\n".join(lines), reply_markup=admin_menu())
    elif action == "match":
        user_state[cb.from_user.id] = {"mode": "match", "data": {}}
        await cb.message.answer("⚽ Где играем?", reply_markup=match_venue_kb())


@dp.callback_query(F.data.startswith("pl:"))
async def player_callbacks(cb: CallbackQuery):
    """Обрабатывает нажатия кнопок в меню управления игроком."""
    if cb.message.chat.type != ChatType.PRIVATE and not await is_admin(cb.message.chat.id, cb.from_user.id):
        await cb.answer("❌ Нет доступа", show_alert=True)
        return
    _, pid, action = cb.data.split(":")
    pid = int(pid)
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass

    if action == "menu":
        p = await db.get_player(pid)
        t = f"<b>{p['name']}</b>"
        if p["player_number"]:
            t += f"\n🔢 № {p['player_number']}"
        if p["birth_date"]:
            t += f"\n🎂 {p['birth_date'][:10]}"
        await cb.message.answer(t, reply_markup=player_menu(pid))
    elif action == "rename":
        user_state[cb.from_user.id] = {"mode": "rename_player", "pid": pid}
        await cb.message.answer("✏️ Новое имя игрока?")
    elif action == "birth":
        user_state[cb.from_user.id] = {"mode": "player_birth", "pid": pid}
        await cb.message.answer("🎂 Дата рождения игрока: ДД.ММ.ГГГГ")
    elif action == "num":
        user_state[cb.from_user.id] = {"mode": "player_num", "pid": pid}
        await cb.message.answer("🔢 Номер игрока?")
    elif action == "delete":
        await db.deactivate_player(pid)
        await cb.message.answer("🗑 Игрок удалён.", reply_markup=admin_menu())


@dp.callback_query(F.data.startswith("mv:"))
async def match_venue_cb(cb: CallbackQuery):
    """Шаг 1 мастера матча: выбор места проведения."""
    venue = cb.data.split(":")[1]
    st = user_state.setdefault(cb.from_user.id, {"mode": "match", "data": {}})
    st["data"]["venue"] = venue
    st["step"] = "score"
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer("⚽ Счёт матча:", reply_markup=score_kb("ms"))


@dp.callback_query(F.data.startswith("ms:"))
async def match_score_cb(cb: CallbackQuery):
    """Шаг 2 мастера матча: выбор счёта."""
    score = cb.data.split(":", 1)[1]
    st = user_state.setdefault(cb.from_user.id, {"mode": "match", "data": {}})
    if score == "custom":
        st["step"] = "score_custom"
        await cb.answer()
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer("✏️ Введи счёт (например 3:2):")
        return
    st["data"]["score"] = score
    st["step"] = "ht"
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer("⚽ Счёт первого тайма:", reply_markup=score_kb("mh"))


@dp.callback_query(F.data.startswith("mh:"))
async def match_ht_cb(cb: CallbackQuery):
    """Шаг 3 мастера матча: выбор счёта первого тайма."""
    score = cb.data.split(":", 1)[1]
    st = user_state.setdefault(cb.from_user.id, {"mode": "match", "data": {}})
    if score == "custom":
        st["step"] = "ht_custom"
        await cb.answer()
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer("✏️ Введи счёт первого тайма (например 1:0):")
        return
    st["data"]["ht_score"] = score
    st["step"] = "date"
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await ephemeral(cb.message, "📅 Дата матча (ДД.ММ):", sec=15)


@dp.callback_query(F.data.startswith("pv:"))
async def poll_vote_cb(cb: CallbackQuery):
    """Обрабатывает голосование в опросе, обновляет отображение."""
    _, pid, opt = cb.data.split(":")
    pid = int(pid)
    opt = int(opt)
    uid = cb.from_user.id
    await ensure_player(uid, cb.from_user.full_name or "Игрок")
    await db.vote_poll(pid, uid, opt)
    poll = await db.get_poll(pid)
    options = poll["options"].split("|||")
    results = await db.get_poll_results(pid)
    lines = [f"📊 <b>{poll['question']}</b>"]
    for i, o in enumerate(options):
        cnt = results.get(i, 0)
        lines.append(f"  {o} — {cnt}")
    lines.append(f"\n✅ Твой выбор: {options[opt]}")
    await cb.answer()
    try:
        await cb.message.edit_text("\n".join(lines))
    except Exception:
        pass


# ═══════════════════════════ ОБРАБОТЧИК ТЕКСТОВОГО ВВОДА ═══════════════════════════


@dp.message(F.text)
async def text_input(msg: Message):
    """
    Принимает текстовый ввод, когда пользователь находится в одном из режимов:
    birth_reg, rename_self, set_teamname, rename_player, player_birth,
    player_num, match (score_custom, ht_custom, date, opponent).
    """
    st = user_state.get(msg.from_user.id)
    if not st:
        return

    mode = st.get("mode")
    text = msg.text.strip()
    try:
        await msg.delete()
    except Exception:
        pass

    # Регистрация даты рождения (для текущего игрока)
    if mode == "birth_reg":
        bd = parse_date(text)
        if not bd:
            await ephemeral(msg, "❌ Неверный формат. Используй ДД.ММ.ГГГГ", sec=8)
            return
        await db.update_player_birth(msg.from_user.id, bd)
        user_state.pop(msg.from_user.id, None)
        await ephemeral(msg, "✅ Дата рождения сохранена!", sec=8)
        await show_stats(msg, msg.from_user.id)

    # Смена имени (текущий игрок)
    elif mode == "rename_self":
        await db.update_player_name(msg.from_user.id, text)
        user_state.pop(msg.from_user.id, None)
        await ephemeral(msg, f"✅ Имя изменено на «{text}»", sec=8)

    # Смена названия команды
    elif mode == "set_teamname":
        await db.set_setting(TEAM_NAME_KEY, text)
        user_state.pop(msg.from_user.id, None)
        await ephemeral(msg, f"🏷 Название команды изменено на «{text}»", sec=8)

    # Переименование игрока (админ)
    elif mode == "rename_player":
        await db.update_player_name(st["pid"], text)
        user_state.pop(msg.from_user.id, None)
        await ephemeral(msg, f"✅ Игрок переименован в «{text}»", sec=8)

    # Дата рождения игрока (админ)
    elif mode == "player_birth":
        bd = parse_date(text)
        if not bd:
            await ephemeral(msg, "❌ Неверный формат. Используй ДД.ММ.ГГГГ", sec=8)
            return
        await db.update_player_birth(st["pid"], bd)
        user_state.pop(msg.from_user.id, None)
        await ephemeral(msg, "✅ Дата рождения сохранена!", sec=8)

    # Номер игрока (админ)
    elif mode == "player_num":
        try:
            n = int(text)
        except ValueError:
            await ephemeral(msg, "❌ Нужно ввести число.", sec=8)
            return
        await db.update_player_number(st["pid"], n)
        user_state.pop(msg.from_user.id, None)
        await ephemeral(msg, f"✅ Игроку присвоен №{n}", sec=8)

    # Мастер матча: ввод счёта (свободный)
    elif mode == "match":
        data = st.setdefault("data", {})
        step = st.get("step")

        if step == "score_custom":
            if ":" not in text:
                await ephemeral(msg, "❌ Формат: 3:2", sec=8)
                return
            data["score"] = text
            st["step"] = "ht_custom"
            await ephemeral(msg, "✏️ Счёт первого тайма?", sec=30)

        elif step == "ht_custom":
            if ":" not in text:
                await ephemeral(msg, "❌ Формат: 1:0", sec=8)
                return
            data["ht_score"] = text
            st["step"] = "date"
            await ephemeral(msg, "📅 Дата матча (ДД.ММ):", sec=30)

        elif step == "date":
            d = parse_date(text)
            if not d:
                await ephemeral(msg, "❌ Неверная дата.", sec=8)
                return
            data["date"] = d
            st["step"] = "opponent"
            await ephemeral(msg, "🏆 С кем играли?", sec=30)

        elif step == "opponent":
            data["opponent"] = text
            our, their = map(int, data["score"].split(":"))
            our_ht, their_ht = map(int, data["ht_score"].split(":"))
            mid = await db.create_match(data["date"], data["opponent"], data.get("venue", "home"))
            await db.update_match_score(mid, our, their, our_ht, their_ht)
            user_state.pop(msg.from_user.id, None)
            d_obj = date.fromisoformat(data["date"])
            d_str = d_obj.strftime("%d.%m.%Y")
            await ephemeral(msg, f"✅ Матч сыгран с «{text}» ({data['score']}) — {d_str}", sec=30)


# ═══════════════════════════ ЗАПУСК ═══════════════════════════


async def main():
    """Точка входа: инициализация БД, установка команд и запуск поллинга."""
    await db.init()
    await set_commands()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(birthday_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())