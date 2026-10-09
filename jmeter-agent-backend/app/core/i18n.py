"""i18n framework — lightweight gettext-style translation for backend.

Supports zh-CN (default) and en. Strings live in JSON files under
i18n/ directory. Use t("key") or t("key", lang="en") to translate.

Design: zero-dependency, file-based, hot-reload in DEBUG mode.

Usage:
    from app.core.i18n import t, set_lang, get_lang
    msg = t("test.completed", concurrency=100, duration=300)
    # zh-CN: "测试完成：100并发，持续300秒"
    # en: "Test completed: 100 concurrency, 300s duration"
"""

import os
import json
import logging
import threading
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n")

_lang: str = settings.I18N_DEFAULT_LANG
_strings: dict[str, dict[str, str]] = {}  # lang -> {key: template}
_lock = threading.Lock()


def _load_lang(lang: str) -> dict[str, str]:
    """Load strings for a language from its JSON file."""
    path = os.path.join(I18N_DIR, f"{lang}.json")
    if not os.path.exists(path):
        logger.warning(f"i18n file not found: {path}")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load i18n file {path}: {e}")
        return {}


def load_all():
    """Load all enabled language files."""
    global _strings
    langs = [l.strip() for l in settings.I18N_ENABLED_LANGS.split(",") if l.strip()]
    with _lock:
        for lang in langs:
            _strings[lang] = _load_lang(lang)
        logger.info(f"i18n loaded: {list(_strings.keys())}")


def reload_lang(lang: str):
    """Hot-reload a single language file."""
    with _lock:
        _strings[lang] = _load_lang(lang)


def set_lang(lang: str):
    global _lang
    if lang not in _strings:
        _strings[lang] = _load_lang(lang)
    _lang = lang


def get_lang() -> str:
    return _lang


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Translate a key. Falls back to key itself if not found.

    Supports Python format-string style interpolation:
        t("error.timeout", tool="execute_test", seconds=30)
        → "工具 execute_test 执行超时 (30秒)"
    """
    use_lang = lang or _lang
    strings = _strings.get(use_lang, {})

    # Try specific lang first, then default lang, then key itself
    template = strings.get(key)
    if template is None and use_lang != settings.I18N_DEFAULT_LANG:
        template = _strings.get(settings.I18N_DEFAULT_LANG, {}).get(key)
    if template is None:
        template = key  # fallback: raw key

    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


# Auto-load on import
load_all()
