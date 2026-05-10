"""Система разрешения алиасов аккаунтов"""

import re
from typing import Optional

from src.config import settings


def find_account_alias(text: str) -> Optional[str]:
    """Ищет алиас аккаунта в тексте по ключевым словам"""
    text_lower = text.lower()
    
    # Ищем фразы типа "от лица X", "как X", "от имени X", "от X"
    patterns = [
        r'от\s+лица\s+([а-яё\s]+)',
        r'как\s+([а-яё\s]+)',
        r'от\s+имени\s+([а-яё\s]+)',
        r'от\s+([а-яё\s]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            alias = match.group(1).strip()
            # Ищем точное совпадение в алиасах (без учета регистра)
            for config_alias, account_id in settings.account_aliases.items():
                if alias.lower() == config_alias.lower():
                    return account_id
    
    return None


def replace_alias_with_account(text: str, account_id: str) -> str:
    """Заменяет алиас на реальный ID аккаунта в тексте"""
    # Находим и удаляем фразы с алиасами
    patterns = [
        r'от\s+лица\s+[а-яё\s]+',
        r'как\s+[а-яё\s]+',
        r'от\s+имени\s+[а-яё\s]+',
        r'от\s+[а-яё\s]+',
    ]
    
    result = text
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    
    # Очищаем от лишних пробелов
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result


def get_all_aliases() -> dict[str, str]:
    """Возвращает все настроенные алиасы"""
    return settings.account_aliases.copy()
