"""Напоминания о Commitment'ах: пинги об overdue и о приближении дедлайна."""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from src.config import settings as app_settings
from src.core.notifier import notifier
from src.core.timeutil import fmt_local
from src.db.models import Commitment
from src.db.repo import get_or_create_user, update_commitment_status
from src.db.session import get_session


logger = logging.getLogger(__name__)


async def _check_once(owner_telegram_id: int) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        s = owner.settings
        if not s.reminders_enabled:
            return

        tz_name = s.timezone
        now = datetime.utcnow()
        lead_hours = max(0, int(s.reminder_lead_hours))
        soon = now + timedelta(hours=lead_hours)

        result = await session.execute(
            select(Commitment).where(
                Commitment.user_id == owner.id,
                Commitment.status.in_(("open", "reminded")),
                Commitment.deadline_at.is_not(None),
            )
        )
        active_commitments = list(result.scalars().all())

        # Задачи, которые пора начать
        result_start = await session.execute(
            select(Commitment).where(
                Commitment.user_id == owner.id,
                Commitment.status.in_(("open", "reminded")),
                Commitment.start_at.is_not(None),
                Commitment.start_at <= now,
                Commitment.start_reminded == False,
            )
        )
        to_start = list(result_start.scalars().all())

    # Сначала обрабатываем старт задач
    for c in to_start:
        who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
        d_str = fmt_local(c.deadline_at, tz_name) if c.deadline_at else "без дедлайна"
        text = (
            f"⏳ <b>Пора начать задачу</b>\n"
            f"<b>{who}</b>: {c.text}\n"
            f"Запланировано до: {d_str}"
        )
        await notifier.notify(text, chat_id=owner_telegram_id)
        async with get_session() as session:
            db_c = await session.get(Commitment, c.id)
            if db_c:
                db_c.start_reminded = True

    if not active_commitments:
        return

    for c in active_commitments:
        d = c.deadline_at
        if d is None:
            continue

        reason = None
        if d < now and s.reminder_overdue_enabled:
            reason = "overdue"
        elif now <= d <= soon and lead_hours > 0 and c.status == "open":
            # Предварительное напоминание отправляем ровно 1 раз
            reason = "lead"

        if reason is None:
            continue

        # Логика повторного спама при просрочке
        if reason == "overdue":
            if c.last_reminded_at:
                # Спамим каждые 5 минут
                if now - c.last_reminded_at < timedelta(minutes=5):
                    continue

        who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
        d_str = fmt_local(c.deadline_at, tz_name)
        
        if reason == "overdue":
            text = (
                f"⏰ <b>Просрочено!</b>\n"
                f"<b>{who}</b>: {c.text}\n"
                f"Срок был: {d_str}"
            )
        else:
            text = (
                f"⏳ <b>Скоро дедлайн</b>\n"
                f"<b>{who}</b>: {c.text}\n"
                f"До: {d_str}"
            )

        # Создаем инлайн клавиатуру для управления напоминанием
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"todo:done:{c.id}"),
        )
        kb.row(
            InlineKeyboardButton(text="⏱ +15м", callback_data=f"todo:postpone:{c.id}:15"),
            InlineKeyboardButton(text="⏱ +1ч", callback_data=f"todo:postpone:{c.id}:60"),
            InlineKeyboardButton(text="⏱ +1д", callback_data=f"todo:postpone:{c.id}:1440"),
        )
        markup = kb.as_markup()

        # Удаляем клавиатуру (кнопки) на прошлом сообщении-напоминании
        if c.last_reminder_msg_id and notifier._bot:
            try:
                await notifier._bot.edit_message_reply_markup(
                    chat_id=owner_telegram_id,
                    message_id=c.last_reminder_msg_id,
                    reply_markup=None
                )
            except Exception:
                pass  # Старое сообщение могло быть удалено пользователем

        # Отправляем новое напоминание
        sent_msg = await notifier.notify(text, reply_markup=markup, chat_id=owner_telegram_id)
        
        # Обновляем состояние обязательства в БД
        async with get_session() as session:
            db_c = await session.get(Commitment, c.id)
            if db_c:
                db_c.status = "reminded"
                db_c.last_reminded_at = now
                if sent_msg:
                    db_c.last_reminder_msg_id = sent_msg.message_id


async def reminders_loop() -> None:
    while True:
        try:
            await _check_once(app_settings.owner_telegram_ids[0])
        except Exception:
            logger.exception("reminders tick failed")
        await asyncio.sleep(10)  # Проверяем каждые 10 секунд
