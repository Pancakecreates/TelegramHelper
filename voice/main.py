import os
import shutil
import tempfile
import asyncio
import logging
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from pydantic_settings import BaseSettings

# Setup logging
logger = logging.getLogger("voice_service")
logging.basicConfig(level=logging.INFO)

class Settings(BaseSettings):
    model_size: str = "small"
    port: int = 8000
    host: str = "0.0.0.0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
app = FastAPI(title="TelegramHelper Voice Transcription API")


@app.on_event("startup")
def startup_event():
    from tunnel import start_tunnel
    start_tunnel()


@app.on_event("shutdown")
def shutdown_event():
    from tunnel import stop_tunnel
    stop_tunnel()

class TranscriptionService:
    def __init__(self, model_size: str) -> None:
        self._model_size = model_size
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure_local_model(self) -> object:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                def _load() -> object:
                    # device="auto" is optimal (GPU if available, CPU otherwise)
                    return WhisperModel(self._model_size, device="auto", compute_type="auto")
                self._model = await asyncio.to_thread(_load)
        return self._model

    async def _transcribe_local(self, path: Path, language: Optional[str]) -> str:
        model = await self._ensure_local_model()
        def _run() -> str:
            segments, _info = model.transcribe(str(path), language=language)
            return " ".join(seg.text.strip() for seg in segments).strip()
        return await asyncio.to_thread(_run)

    async def _transcribe_api(self, path: Path, openai_key: str, language: Optional[str]) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        with path.open("rb") as f:
            resp = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=language,
            )
        return resp.text

    async def transcribe(
        self,
        path: Path,
        mode: str = "hybrid",
        openai_key: Optional[str] = None,
        language: Optional[str] = None,
    ) -> str:
        if mode == "api":
            if not openai_key:
                raise ValueError("OpenAI API key required for transcription mode='api'")
            return await self._transcribe_api(path, openai_key, language)
        elif mode == "local":
            return await self._transcribe_local(path, language)
        else:  # hybrid
            try:
                return await self._transcribe_local(path, language)
            except Exception as e:
                logger.exception("Local transcription failed, falling back to API: %s", e)
                if openai_key:
                    return await self._transcribe_api(path, openai_key, language)
                else:
                    raise

transcription_service = TranscriptionService(model_size=settings.model_size)

@app.get("/health")
async def health_check():
    return {"status": "ok", "model_size": settings.model_size}

@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    mode: str = Form("hybrid"),
    openai_key: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
):
    # Ensure temporary folder exists
    temp_dir = Path("temp")
    temp_dir.mkdir(exist_ok=True)
    
    # Save UploadFile to a temporary file
    suffix = Path(file.filename).suffix or ".ogg"
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir)
    temp_path = Path(temp_file.name)
    
    try:
        # Write file content
        with temp_file as f:
            shutil.copyfileobj(file.file, f)
        
        # Transcribe
        text = await transcription_service.transcribe(
            temp_path,
            mode=mode,
            openai_key=openai_key or None,
            language=language or None,
        )
        return {"text": text}
    except Exception as e:
        logger.exception("Error during transcription")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
    finally:
        # Ensure temporary file is cleaned up
        if temp_path.exists():
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.warning("Could not delete temporary file %s: %s", temp_path, e)
