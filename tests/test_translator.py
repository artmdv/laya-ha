"""Unit tests for Laya translator module."""

import unittest
from unittest.mock import AsyncMock, MagicMock
from custom_components.laya.translator import async_translate_to_english


class TestTranslator(unittest.IsolatedAsyncioTestCase):
    """Test translator utilities."""

    async def test_empty_string_returns_empty(self):
        """Empty string returns unchanged."""
        session = MagicMock()
        res = await async_translate_to_english("", session)
        self.assertEqual(res, "")

    async def test_google_gtx_translation_success(self):
        """Verify parsing of Google GTX JSON response."""
        session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value=[[["turn off the light in the kitchen", "išjunk šviesą virtuvėj", None, None]]]
        )
        session.get.return_value.__aenter__.return_value = mock_resp

        res = await async_translate_to_english("išjunk šviesą virtuvėj", session, source_lang="lt")
        self.assertEqual(res, "turn off the light in the kitchen")

    async def test_custom_libretranslate_success(self):
        """Verify custom LibreTranslate URL is used if provided."""
        session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"translatedText": "turn on the light in the kitchen"})
        session.post.return_value.__aenter__.return_value = mock_resp

        res = await async_translate_to_english(
            "įjunk šviesą virtuvėj",
            session,
            custom_url="http://localhost:5000/translate",
            source_lang="lt",
        )
        self.assertEqual(res, "turn on the light in the kitchen")
        session.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
