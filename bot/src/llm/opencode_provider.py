"""OpenCode провайдер для локального API."""

import asyncio
import json
import logging
from typing import Any

import aiohttp

from src.config import LLMDefaults
from src.llm.base import ChatMessage, LLMProvider


logger = logging.getLogger(__name__)


class OpenCodeProvider(LLMProvider):
    """Провайдер для локального OpenCode API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _close_session(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def embed(self, text: str) -> list[float]:
        """OpenCode API не поддерживает эмбеддинги, используем заглушку."""
        logger.warning("OpenCode провайдер не поддерживает эмбеддинги, возвращаю пустой вектор")
        return [0.0] * 1536  # Стандартный размер для совместимости

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        """Отправляет сообщение в OpenCode API."""
        session = await self._ensure_session()
        
        try:
            # 1. Создаём сессию
            session_response = await session.post(
                f"{self.base_url}/session",
                headers={"Content-Type": "application/json"},
                json={"title": "TelegramBot", "agent": "general"}
            )
            session_response.raise_for_status()
            session_data = await session_response.json()
            session_id = session_data["id"]
            
            # 2. Формируем части сообщения (все роли: system + user)
            parts = []
            for msg in messages:
                if msg.content:
                    parts.append({"type": "text", "text": msg.content})
            
            if not parts:
                parts.append({"type": "text", "text": messages[-1].content if messages else ""})
            
            # TODO: Добавить поддержку изображений если нужно в будущем
            # Бот может получать фото от пользователя и передавать их в OpenCode
            
            # 3. Отправляем через prompt_async
            await session.post(
                f"{self.base_url}/session/{session_id}/prompt_async",
                headers={"Content-Type": "application/json"},
                json={
                    "parts": parts,
                    "model": {
                        "providerID": "opencode",
                        "modelID": LLMDefaults.OPENCODE_CHAT_LIGHT  # OpenCode всегда использует легкую модель
                    }
                }
            )
            
            # 4. Ждём ответа
            max_attempts = 60  # 60 секунд максимум
            for attempt in range(max_attempts):
                await asyncio.sleep(1)
                
                msgs_response = await session.get(
                    f"{self.base_url}/session/{session_id}/message?order=desc&limit=1"
                )
                msgs_response.raise_for_status()
                messages_data = await msgs_response.json()
                
                last_msg = messages_data[0] if messages_data else None
                if (last_msg and 
                    last_msg.get("info", {}).get("role") == "assistant" and 
                    last_msg.get("info", {}).get("finish") == "stop"):
                    
                    # Извлекаем текст ответа
                    for part in last_msg.get("parts", []):
                        if part.get("type") == "text":
                            return part.get("text", "")
                    
                    break
            
            logger.warning("Не удалось получить ответ от OpenCode API за отведенное время")
            return "Извините, не удалось получить ответ от локального API."
            
        except aiohttp.ClientError as e:
            logger.exception("Ошибка при запросе к OpenCode API")
            return f"Ошибка подключения к локальному API: {e}"
        except Exception as e:
            logger.exception("Непредвиденная ошибка в OpenCode провайдере")
            return f"Ошибка в локальном API: {e}"
        finally:
            # Закрываем сессию
            try:
                await session.delete(f"{self.base_url}/session/{session_id}")
            except:
                pass

    async def close(self) -> None:
        """Закрывает HTTP сессию."""
        await self._close_session()

    def __del__(self):
        """Деструктор для очистки ресурсов."""
        if self.session and not self.session.closed:
            # Создаем задачу для закрытия сессии
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._close_session())
                else:
                    loop.run_until_complete(self._close_session())
            except:
                pass
