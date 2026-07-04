import os
import sys
import shutil
import urllib.request
import subprocess
import logging
import atexit
from pathlib import Path

logger = logging.getLogger("voice_tunnel")


cloudflared_proc = None


def is_disabled() -> bool:
    v = os.environ.get("CLOUDFLARE_TUNNEL", "").strip().lower()
    return v in ("0", "false", "off", "no")


def ensure_cloudflared() -> str:
    # 1. Проверяем в системном PATH
    system_path = shutil.which("cloudflared")
    if system_path:
        return system_path
        
    # 2. Проверяем локально в корне
    binary_name = "cloudflared.exe" if sys.platform.startswith("win32") else "cloudflared"
    local_path = Path(binary_name).resolve()
    if local_path.exists():
        return str(local_path)
        
    # 3. Скачиваем официальный бинарник
    logger.info("cloudflared не найден. Скачиваем...")
    if sys.platform.startswith("win32"):
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    elif sys.platform.startswith("linux"):
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    else:
        raise OSError(f"Неподдерживаемая ОС: {sys.platform}")
        
    try:
        urllib.request.urlretrieve(url, binary_name)
        if not sys.platform.startswith("win32"):
            os.chmod(binary_name, 0o755)
        logger.info("cloudflared успешно скачан и настроен.")
        return str(local_path)
    except Exception as e:
        logger.error(f"Не удалось скачать cloudflared: {e}")
        raise


def start_tunnel():
    global cloudflared_proc
    if is_disabled():
        logger.info("☁️ Cloudflare Tunnel: отключён через CLOUDFLARE_TUNNEL=0/false/off")
        return
        
    token = os.environ.get("CLOUDFLARE_TUNNEL_TOKEN")
    if not token:
        logger.info("☁️ Cloudflare Tunnel: CLOUDFLARE_TUNNEL_TOKEN не установлен, пропуск.")
        return
        
    try:
        binary_path = ensure_cloudflared()
    except Exception:
        logger.error("❌ [туннель] не удалось настроить cloudflared")
        return
        
    logger.info("")
    logger.info("═══════════════════════════════════════════════")
    logger.info("☁️  Cloudflare Tunnel: запуск по токену")
    logger.info("═══════════════════════════════════════════════")
    logger.info(f"[туннель] токен: {token[:20]}...")
    logger.info("═══════════════════════════════════════════════")
    
    try:
        # Запуск процесса. stdio наследуется (выводится в консоль Pterodactyl)
        cloudflared_proc = subprocess.Popen(
            [binary_path, "tunnel", "run", "--token", token],
            preexec_fn=None if sys.platform.startswith("win32") else os.setsid
        )
        logger.info(f"[туннель] PID процесса: {cloudflared_proc.pid}")
        logger.info("")
    except Exception as e:
        logger.error(f"❌ [туннель] не удалось запустить: {e}")


def stop_tunnel():
    global cloudflared_proc
    if cloudflared_proc and cloudflared_proc.poll() is None:
        logger.info("[туннель] остановка...")
        try:
            if sys.platform.startswith("win32"):
                cloudflared_proc.terminate()
            else:
                import signal
                os.killpg(os.getpgid(cloudflared_proc.pid), signal.SIGTERM)
            cloudflared_proc.wait(timeout=5)
        except Exception:
            try:
                cloudflared_proc.kill()
            except Exception:
                pass
        logger.info("[туннель] остановлен.")
        cloudflared_proc = None


atexit.register(stop_tunnel)
