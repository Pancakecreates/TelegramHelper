import pytz
from datetime import datetime, timedelta
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from src.bot.filters import OwnerOnly
from src.core.timeutil import fmt_local
from src.db.models import Commitment
from src.db.repo import (
    get_or_create_user,
    list_open_commitments,
    update_commitment_status,
)
from src.db.session import get_session


router = Router(name="todos")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def _format(c, tz_name: str) -> str:
    who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
    if c.start_at and c.deadline_at:
        start = fmt_local(c.start_at, tz_name)
        deadline = fmt_local(c.deadline_at, tz_name)
        return f"<b>{who}</b> · {c.text} (с {start} до {deadline})"
    elif c.start_at:
        start = fmt_local(c.start_at, tz_name)
        return f"<b>{who}</b> · {c.text} (старт {start})"
    elif c.deadline_at:
        deadline = fmt_local(c.deadline_at, tz_name)
        return f"<b>{who}</b> · {c.text} (до {deadline})"
    return f"<b>{who}</b> · {c.text} (без срока)"


def fmt_time_only(dt: datetime, tz_name: str) -> str:
    if dt is None:
        return ""
    local_tz = pytz.timezone(tz_name)
    local_dt = pytz.utc.localize(dt).astimezone(local_tz)
    return local_dt.strftime("%H:%M")


def _format_schedule_item(c, tz_name: str) -> str:
    who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
    status_icon = "✅" if c.status == "done" else "⏳"
    
    time_str = ""
    if c.start_at and c.deadline_at:
        t_start = fmt_time_only(c.start_at, tz_name)
        t_end = fmt_time_only(c.deadline_at, tz_name)
        time_str = f"🕒 <b>{t_start} - {t_end}</b>"
    elif c.start_at:
        t_start = fmt_time_only(c.start_at, tz_name)
        time_str = f"🎬 <b>{t_start} (старт)</b>"
    elif c.deadline_at:
        t_end = fmt_time_only(c.deadline_at, tz_name)
        time_str = f"🏁 <b>{t_end} (дедлайн)</b>"
        
    return f"{status_icon} {time_str} · <b>{who}</b>: {c.text}"


def _get_date_range_utc(date_str: str, tz_name: str) -> tuple[datetime, datetime, str]:
    user_tz = pytz.timezone(tz_name)
    now_local = datetime.now(user_tz)
    
    date_str = date_str.lower().strip()
    if not date_str or date_str in ("сегодня", "today"):
        target_date = now_local.date()
        label = "сегодня"
    elif date_str in ("завтра", "tomorrow"):
        target_date = (now_local + timedelta(days=1)).date()
        label = "завтра"
    elif date_str in ("вчера", "yesterday"):
        target_date = (now_local - timedelta(days=1)).date()
        label = "вчера"
    else:
        # Парсим YYYY-MM-DD или DD.MM.YYYY
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            label = date_str
        except ValueError:
            try:
                target_date = datetime.strptime(date_str, "%d.%m.%Y").date()
                label = date_str
            except ValueError:
                raise ValueError("Неверный формат даты. Используйте: сегодня, завтра, YYYY-MM-DD или DD.MM.YYYY")
                
    start_local = user_tz.localize(datetime.combine(target_date, datetime.min.time()))
    end_local = user_tz.localize(datetime.combine(target_date, datetime.max.time()))
    
    start_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(pytz.utc).replace(tzinfo=None)
    return start_utc, end_utc, label


def _kb(commitment_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"todo:done:{commitment_id}"),
        InlineKeyboardButton(text="🚫 Отменить", callback_data=f"todo:cancel:{commitment_id}"),
    )
    return kb.as_markup()


@router.message(Command("todos"))
async def cmd_todos(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    date_arg = args[1] if len(args) > 1 else None
    
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        tz_name = owner.settings.timezone
        
    if date_arg:
        try:
            start_utc, end_utc, label = _get_date_range_utc(date_arg, tz_name)
        except ValueError as e:
            await message.answer(str(e))
            return
            
        async with get_session() as session:
            result = await session.execute(
                select(Commitment)
                .where(
                    Commitment.user_id == owner.id,
                    Commitment.status != "cancelled",
                    (
                        ((Commitment.start_at >= start_utc) & (Commitment.start_at <= end_utc)) |
                        ((Commitment.deadline_at >= start_utc) & (Commitment.deadline_at <= end_utc))
                    )
                )
            )
            items = list(result.scalars().all())
            
        if not items:
            await message.answer(f"📅 На {label} задач не запланировано 😴")
            return
            
        # Сортируем по времени старта или дедлайна
        items.sort(key=lambda c: c.start_at or c.deadline_at or datetime.min)
        
        await message.answer(f"📅 <b>График задач на {label}:</b>")
        for c in items:
            kb_markup = None
            if c.status in ("open", "reminded"):
                kb_markup = _kb(c.id)
            await message.answer(_format_schedule_item(c, tz_name), reply_markup=kb_markup)
            
    else:
        async with get_session() as session:
            items = await list_open_commitments(session, owner)
            
        if not items:
            await message.answer("Открытых обязательств нет 🎉")
            return

        await message.answer(f"📋 Открытых обязательств: <b>{len(items)}</b>")
        for c in items[:30]:
            await message.answer(_format(c, tz_name), reply_markup=_kb(c.id))


@router.callback_query(F.data.startswith("todo:done:"))
async def cb_done(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    async with get_session() as session:
        await update_commitment_status(session, cid, "done")
    if callback.message:
        await callback.message.edit_text(callback.message.html_text + "\n\n✅ Готово")
    await callback.answer()


@router.callback_query(F.data.startswith("todo:cancel:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    async with get_session() as session:
        await update_commitment_status(session, cid, "cancelled")
    if callback.message:
        await callback.message.edit_text(callback.message.html_text + "\n\n🚫 Отменено")
    await callback.answer()


@router.callback_query(F.data.startswith("todo:postpone:"))
async def cb_postpone(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    cid = int(parts[2])
    mins = int(parts[3])
    new_deadline = datetime.utcnow() + timedelta(minutes=mins)
    
    async with get_session() as session:
        c = await session.get(Commitment, cid)
        if c is not None:
            c.deadline_at = new_deadline
            c.status = "open"
            c.last_reminder_msg_id = None
            c.last_reminded_at = None
            
    if callback.message:
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            tz_name = owner.settings.timezone
        d_str = fmt_local(new_deadline, tz_name)
        await callback.message.edit_text(
            callback.message.html_text + f"\n\n⏱ Перенесено на {mins} мин (до {d_str})"
        )
    await callback.answer(f"Перенесено на {mins} минут")
