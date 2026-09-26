"""Translation utilities for Laya System-1 Conversation."""

from __future__ import annotations

import logging
import aiohttp

_LOGGER = logging.getLogger(__name__)

GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"


async def async_translate_to_english(
    text: str,
    session: aiohttp.ClientSession,
    custom_url: str | None = None,
    source_lang: str = "auto",
) -> str:
    """Translate prompt to English using Google GTX or custom LibreTranslate endpoint."""
    text_clean = text.strip()
    if not text_clean:
        return text

    # 1. Custom LibreTranslate endpoint if configured
    if custom_url and custom_url.strip():
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
            _LOGGER.warning("Custom translation endpoint failed (%s), falling back to Google: %s", custom_url, err)

    # 2. Google GTX endpoint (ultra-fast ~50ms, zero auth, zero config)
    try:
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": "en",
            "dt": "t",
            "q": text_clean,
        }
        async with session.get(
            GOOGLE_TRANSLATE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=3.0),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    translated_parts = [
                        part[0]
                        for part in data[0]
                        if part and len(part) > 0 and isinstance(part[0], str)
                    ]
                    if translated_parts:
                        return "".join(translated_parts).strip()
    except Exception as err:
        _LOGGER.warning("Translation to English failed: %s", err)

    return text_clean
