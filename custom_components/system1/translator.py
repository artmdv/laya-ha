"""Local translation utilities for Laya System-1 Conversation."""

from __future__ import annotations

import logging
import aiohttp

_LOGGER = logging.getLogger(__name__)


async def async_translate_to_english(
    text: str,
    session: aiohttp.ClientSession,
    custom_url: str | None = None,
    source_lang: str = "auto",
) -> str:
    """Translate prompt to English using a local self-hosted endpoint (e.g. LibreTranslate/Ollama)."""
    text_clean = text.strip()
    if not text_clean or not custom_url or not custom_url.strip():
        return text_clean

    try:
        payload = {
            "q": text_clean,
            "source": source_lang if source_lang != "auto" else "auto",
            "target": "en",
            "format": "text",
        }
        async with session.post(
            custom_url.strip(),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=4.0),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                translated = data.get("translatedText")
                if translated:
                    return translated.strip()
    except Exception as err:
        _LOGGER.warning("Local translation endpoint failed (%s): %s", custom_url, err)

    return text_clean

