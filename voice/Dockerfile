FROM python:3.12-slim

# ffmpeg is needed for converting audio/video note formats
# libgomp1 is the OpenMP runtime for ctranslate2 (faster-whisper dependency)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       libgomp1 \
       ca-certificates \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XDG_CACHE_HOME=/app/cache \
    HF_HOME=/app/cache/huggingface

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY main.py ./

# Create directory for caching models and temporary files
RUN mkdir -p /app/cache /app/temp

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
