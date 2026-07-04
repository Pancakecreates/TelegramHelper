import os
import sys
import shutil
import urllib.request
import tarfile
import logging
from pathlib import Path

logger = logging.getLogger("ffmpeg_helper")


def ensure_ffmpeg():
    # 1. Проверяем наличие ffmpeg в системе
    if shutil.which("ffmpeg") is not None:
        logger.info("Системный ffmpeg обнаружен.")
        return

    # 2. Проверяем наличие локального ffmpeg в папке проекта
    local_ffmpeg = Path("ffmpeg")
    if local_ffmpeg.exists():
        os.environ["PATH"] += os.pathsep + os.path.abspath(".")
        logger.info("Локальный ffmpeg обнаружен и добавлен в PATH.")
        return

    # 3. Скачиваем статический бинарник для Linux, если мы на Linux
    if sys.platform.startswith("linux"):
        logger.info("ffmpeg не найден. Скачиваем статический бинарник для Linux...")
        try:
            url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
            tar_path = Path("ffmpeg.tar.xz")
            
            # Скачивание файла
            urllib.request.urlretrieve(url, tar_path)
            
            # Распаковываем только исполняемые файлы ffmpeg и ffprobe в корень проекта
            with tarfile.open(tar_path, "r:xz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("/ffmpeg") or member.name.endswith("/ffprobe"):
                        member.name = Path(member.name).name  # распаковываем без вложенных папок
                        tar.extract(member, path=".")
            
            # Выдаем права на запуск
            os.chmod("ffmpeg", 0o755)
            if os.path.exists("ffprobe"):
                os.chmod("ffprobe", 0o755)
                
            # Удаляем архив
            tar_path.unlink()
            
            # Добавляем в PATH
            os.environ["PATH"] += os.pathsep + os.path.abspath(".")
            logger.info("Локальный ffmpeg успешно настроен.")
        except Exception as e:
            logger.error(f"Не удалось скачать статический ffmpeg: {e}")
