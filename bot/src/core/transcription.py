import asyncio
import logging
from pathlib import Path
import httpx

from src.config import settings
from src.db.repo import cache_transcript, get_cached_transcript
from src.db.session import get_session

logger = logging.getLogger(__name__)


class TranscriptionService:
    """Сервис транскрипции голоса, делегирующий работу внешнему voice-service."""

    async def transcribe(
        self,
        path: Path,
        *,
        file_id: str | None = None,
        mode: str = "hybrid",
        openai_key: str | None = None,
        language: str | None = None,
    ) -> str:
        # file_id используется как ключ кэша — обычно telegram media file_unique_id
        if file_id:
            async with get_session() as session:
                cached = await get_cached_transcript(session, file_id)
                if cached:
                    logger.info("Found cached transcript for file_id: %s", file_id)
                    return cached

        text = ""
        url = f"{settings.voice_service_url.rstrip('/')}/transcribe"
        
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                with path.open("rb") as f:
                    files = {"file": (path.name, f, "application/octet-stream")}
                    data = {
                        "mode": mode,
                        "openai_key": openai_key or "",
                        "language": language or "",
                    }
                    response = await client.post(url, files=files, data=data)
                    response.raise_for_status()
                    result = response.json()
                    text = result.get("text", "")
        except Exception as e:
            logger.exception("Failed to transcribe file %s via voice-service", path.name)
            raise

        if file_id and text:
            async with get_session() as session:
                await cache_transcript(session, file_id, text)
        return text


transcription_service = TranscriptionService()
