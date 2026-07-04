from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.db.models import Base


engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# SQLite FTS5: virtual table + триггеры синхронизации с messages.
# Хранит rowid = messages.id.
_FTS_SETUP = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        text,
        transcript,
        extracted_text,
        sender_name,
        content='messages',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
]


async def init_db() -> None:
    if "sqlite" in settings.database_url:
        from sqlalchemy.engine.url import make_url
        url = make_url(settings.database_url)
        if url.database:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    settings.data_dir  # триггерит создание директории
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN business_connection_id VARCHAR(128)"))
        except Exception:
            pass  # Уже существует
        try:
            await conn.execute(text("ALTER TABLE commitments ADD COLUMN last_reminded_at DATETIME"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TABLE commitments ADD COLUMN last_reminder_msg_id BIGINT"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TABLE commitments ADD COLUMN start_at DATETIME"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TABLE commitments ADD COLUMN start_reminded BOOLEAN DEFAULT 0"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TABLE commitments ADD COLUMN last_start_reminded_at DATETIME"))
        except Exception:
            pass
        try:
            await conn.execute(text("UPDATE user_settings SET timezone = 'Europe/Moscow' WHERE timezone = 'UTC'"))
        except Exception:
            pass

        for stmt in _FTS_SETUP:
            await conn.execute(text(stmt))


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
