import logging
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, BusinessConnection
from sqlalchemy import select

from src.config import settings as app_settings
from src.db.models import User, Message as DbMessage, Contact, AutoReplyLog, UserSettings
from src.db.repo import get_or_create_user, upsert_contact, upsert_message, get_contact, add_auto_reply_log
from src.db.session import get_session
from src.core.transcription import transcription_service
from src.core.documents import extract_text, is_supported
from src.core.notifier import notifier
from src.llm.base import ChatMessage
from src.llm.router import build_provider
from src.core.style_profile import style_profile_as_prompt_hint

logger = logging.getLogger(__name__)
router = Router(name="business")

COOLDOWN_MINUTES = 30
CONTEXT_LIMIT = 20

AUTO_REPLY_SYSTEM_BASE = (
    "Ты отвечаешь от моего имени, потому что я сейчас оффлайн. Будь краток, дружелюбен.\n"
    "Если просят что-то конкретное — не обещай за меня, скажи: «передам, сейчас занят».\n"
    "Если уточняющий вопрос явно нужен — задай его.\n"
    "Никогда не выдавай, что ты ИИ. Не упоминай ассистента.\n"
    "Не пиши длиннее 1–3 коротких предложений."
)

def _classify_aiogram(msg: Message) -> str:
    if msg.voice:
        return "voice"
    if msg.video_note:
        return "video_note"
    if msg.audio:
        return "audio"
    if msg.document:
        return "document"
    if msg.photo:
        return "photo"
    if msg.text:
        return "text"
    return "other"

async def _recently_replied(owner_id: int, peer_id: int, cooldown_min: int) -> bool:
    threshold = datetime.utcnow() - timedelta(minutes=cooldown_min)
    async with get_session() as session:
        result = await session.execute(
            select(AutoReplyLog)
            .where(
                AutoReplyLog.user_id == owner_id,
                AutoReplyLog.peer_id == peer_id,
                AutoReplyLog.created_at >= threshold,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

async def _load_history_from_db(owner_id: int, peer_id: int, limit: int = 20) -> str:
    async with get_session() as session:
        result = await session.execute(
            select(DbMessage)
            .where(DbMessage.user_id == owner_id, DbMessage.peer_id == peer_id)
            .order_by(DbMessage.date.desc())
            .limit(limit)
        )
        db_msgs = list(result.scalars().all())
    
    db_msgs.reverse()
    lines = []
    for m in db_msgs:
        sender = m.sender_name or ("Вы" if m.is_outgoing else "Собеседник")
        text = m.text or (f"[{m.kind}]" if m.kind != "text" else "")
        if m.transcript:
            text += f" (Голосовое: {m.transcript})"
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)

async def _build_reply_text(
    owner_telegram_id: int,
    owner_id: int,
    peer_id: int,
    sender_name: str,
    incoming_text: str,
) -> Optional[str]:
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        provider = await build_provider(session, owner)
        contact = await get_contact(session, owner, peer_id)
        heavy = owner.settings.use_heavy_model

    if provider is None:
        logger.warning("auto-reply: no LLM provider configured")
        return None

    # Загружаем историю последних сообщений из нашей БД
    history_text = await _load_history_from_db(owner_id, peer_id, limit=CONTEXT_LIMIT)

    style_hint = style_profile_as_prompt_hint(contact.style_profile if contact else None)
    system = AUTO_REPLY_SYSTEM_BASE + ("\n" + style_hint if style_hint else "")

    user_prompt = (
        f"Собеседник: {sender_name}.\n"
        f"Контекст последних сообщений:\n{history_text}\n\n"
        f"Последнее входящее: {incoming_text}\n\n"
        "Сформируй ответ от моего имени."
    )
    try:
        return await provider.chat(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user_prompt),
            ],
            heavy=heavy,
        )
    except Exception:
        logger.exception("auto-reply: LLM call failed")
        return None

async def run_auto_reply(message: Message, owner: User, display_name: str, incoming_text: str) -> None:
    s: UserSettings = owner.settings
    if not s.auto_reply_enabled:
        return

    async with get_session() as session:
        existing = await get_contact(session, owner, message.chat.id)
        if s.ignore_archived and existing is not None and existing.is_archived:
            return

    cooldown = s.auto_reply_cooldown_min
    if await _recently_replied(owner.id, message.chat.id, cooldown):
        return

    mode = s.auto_reply_mode
    static_text = s.auto_reply_text or ""

    if mode == "smart":
        reply = await _build_reply_text(
            owner.telegram_id, owner.id, message.chat.id, display_name, incoming_text
        )
        if not reply:
            return
    else:  # static (default)
        reply = static_text.strip()
        if not reply:
            return

    # Отправляем сообщение от имени бизнес-аккаунта
    await message.bot.send_message(
        chat_id=message.chat.id,
        text=reply,
        business_connection_id=message.business_connection_id
    )

    async with get_session() as session:
        owner_refreshed = await get_or_create_user(session, owner.telegram_id)
        await add_auto_reply_log(
            session,
            user_id=owner_refreshed.id,
            peer_id=message.chat.id,
            peer_name=display_name,
            incoming_text=incoming_text[:500],
            reply_text=reply,
        )

    await notifier.notify(
        f"🤖 <b>Авто-ответ</b> для <b>{display_name}</b>\n\n"
        f"<i>Им:</i> {incoming_text[:200]}\n"
        f"<i>Я:</i> {reply}"
    )

@router.business_connection()
async def handle_business_connection(connection: BusinessConnection):
    logger.warning("!!! BUSINESS CONNECTION EVENT: user_id=%s, conn_id=%s, is_enabled=%s", connection.user.id, connection.id, connection.is_enabled)
    async with get_session() as session:
        owner = await get_or_create_user(session, connection.user.id)
        if connection.is_enabled:
            owner.business_connection_id = connection.id
            logger.warning("!!! Saved business_connection_id=%s for user_id=%s in DB", connection.id, connection.user.id)
        else:
            owner.business_connection_id = None
            logger.warning("!!! Cleared business_connection_id for user_id=%s in DB", connection.user.id)

@router.business_message()
async def handle_business_message(message: Message):
    async with get_session() as session:
        # Находим владельца этого подключения в БД
        result = await session.execute(
            select(User).where(User.business_connection_id == message.business_connection_id)
        )
        owner = result.scalar_one_or_none()
        if owner is None:
            logger.warning("Received business message for unknown connection: %s", message.business_connection_id)
            return

        peer_id = message.chat.id
        peer_kind = "user"
        if message.chat.type in ("group", "supergroup"):
            peer_kind = "chat"
        elif message.chat.type == "channel":
            peer_kind = "channel"

        is_bot = bool(message.from_user.is_bot) if message.from_user else False

        # Определяем отображаемое имя
        if message.from_user:
            parts = [message.from_user.first_name, message.from_user.last_name]
            display_name = " ".join(p for p in parts if p).strip() or message.from_user.username or str(message.from_user.id)
            username = message.from_user.username
        else:
            display_name = message.chat.title or str(peer_id)
            username = message.chat.username

        await upsert_contact(
            session,
            owner,
            peer_id=peer_id,
            peer_kind=peer_kind,
            is_bot=is_bot,
            display_name=display_name,
            username=username,
        )

        is_outgoing = bool(message.from_user and message.from_user.id == owner.telegram_id)
        
        sender_name = None
        if not is_outgoing and message.from_user:
            parts = [message.from_user.first_name, message.from_user.last_name]
            sender_name = " ".join(p for p in parts if p).strip() or message.from_user.username or str(message.from_user.id)

        kind = _classify_aiogram(message)
        text = message.text or message.caption or None
        transcript = None
        extracted_text = None

        # Обрабатываем ГЧ / Аудио / Кружочки в реальном времени
        if kind in {"voice", "audio", "video_note"}:
            try:
                media = message.voice or message.audio or message.video_note
                if media:
                    ext = ".mp4" if message.video_note else ".ogg"
                    media_dir = app_settings.data_dir / "media" / str(owner.telegram_id)
                    media_dir.mkdir(parents=True, exist_ok=True)
                    target = media_dir / f"{message.message_id}_{media.file_unique_id}{ext}"
                    
                    await message.bot.download(media.file_id, destination=str(target))
                    
                    mode = owner.settings.transcription_mode
                    from src.db.repo import get_api_key
                    openai_key = await get_api_key(session, owner, "openai")
                    
                    transcript = await transcription_service.transcribe(
                        target,
                        file_id=media.file_unique_id,
                        mode=mode,
                        openai_key=openai_key,
                    )
            except Exception:
                logger.exception("Failed to transcribe media in business_message handler")

        # Обрабатываем документы в реальном времени
        elif kind == "document" and message.document:
            try:
                doc = message.document
                if doc and is_supported(doc.file_name or ""):
                    media_dir = app_settings.data_dir / "media" / str(owner.telegram_id)
                    media_dir.mkdir(parents=True, exist_ok=True)
                    target = media_dir / f"{message.message_id}_{doc.file_name}"
                    await message.bot.download(doc.file_id, destination=str(target))
                    extracted_text = await extract_text(target)
            except Exception:
                logger.exception("Failed to parse doc in business_message handler")

        # Сохраняем в БД
        await upsert_message(
            session,
            user_id=owner.id,
            peer_id=peer_id,
            message_id=message.message_id,
            sender_id=message.from_user.id if message.from_user else None,
            sender_name=sender_name,
            is_outgoing=is_outgoing,
            date=message.date.replace(tzinfo=None) if message.date else datetime.utcnow(),
            kind=kind,
            text=text,
            transcript=transcript,
            media_path=None,
            extracted_text=extracted_text,
        )

    # Запуск авто-ответа для входящих ЛС
    if not is_outgoing and message.chat.type == "private":
        incoming_text = text or (f"[Голосовое: {transcript}]" if transcript else f"[{kind}]")
        await run_auto_reply(message, owner, display_name, incoming_text)
