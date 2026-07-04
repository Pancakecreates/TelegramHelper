import logging
from typing import TYPE_CHECKING

from src.config import settings


if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message


logger = logging.getLogger(__name__)


class Notifier:
    # шлёт сообщения владельцу через control bot — используется userbot-кодом

    def __init__(self) -> None:
        self._bot: "Bot | None" = None

    def attach(self, bot: "Bot") -> None:
        self._bot = bot

    async def notify(self, text: str, *, parse_mode: str | None = "HTML", reply_markup = None) -> "Message | None":
        if self._bot is None:
            logger.warning("Notifier not attached, dropping message: %s", text[:80])
            return None
        try:
            return await self._bot.send_message(
                chat_id=settings.owner_telegram_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except Exception:
            logger.exception("Failed to notify owner")
            return None


notifier = Notifier()
