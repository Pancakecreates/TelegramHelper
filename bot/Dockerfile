FROM python:3.12-slim

# ca-certificates и tzdata нужны для безопасных сетевых запросов и временных зон
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/app/data/cache \
    HF_HOME=/app/data/cache/huggingface

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY src/ ./src/
COPY main.py ./

# data — монтируется томом снаружи (БД, сессии, qdrant, media, кэш моделей)
RUN mkdir -p /app/data

CMD ["python", "main.py"]
