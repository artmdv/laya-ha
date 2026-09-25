import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

if "homeassistant" not in sys.modules:
    ha_mock = MagicMock()

    class _MockConversationEntity:
        pass

    ha_mock.ConversationEntity = _MockConversationEntity
    ha_mock.components.conversation.ConversationEntity = _MockConversationEntity

    for mod in [
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.helpers.config_validation",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.intent",
        "homeassistant.components",
        "homeassistant.components.conversation",
        "homeassistant.data_entry_flow",
    ]:
        sys.modules[mod] = ha_mock

from custom_components.laya.client import (
    DecisionChoice,
    LayaApiError,
    LayaAuthError,
    LayaClient,
    LayaConnectionError,
    LayaDecision,
    LayaTimeoutError,
)


class TestLayaClient(unittest.IsolatedAsyncioTestCase):
    """Test suite for LayaClient communication and parsing."""

    def setUp(self):
        self.base_url = "http://localhost:8000"
        self.client = LayaClient(base_url=self.base_url, timeout=2.0)

    async def asyncTearDown(self):
        await self.client.close()

    def test_headers_without_auth(self):
        """Test header building without API key."""
        headers = self.client._get_headers()
        self.assertEqual(headers, {"Content-Type": "application/json"})

    def test_headers_with_auth(self):
        """Test header building with API key."""
        auth_client = LayaClient(base_url=self.base_url, api_key="secret-key-123")
        headers = auth_client._get_headers()
        self.assertEqual(
            headers,
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-key-123",
            },
        )

    def test_parse_response_success(self):
        """Test parsing valid /v1/systemone response."""
        mock_data = {
            "model": "laya-rl-agent",
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": "light.turn_off",
                    "confidence": 0.54,
                    "answer_confidence": 0.8143,
                    "probabilities": {
                        "light.turn_on": 0.18,
                        "light.turn_off": 0.8143,
                    },
                },
                "target": {
                    "type": "choice",
                    "choice": "Living Room",
                    "confidence": 0.62,
                    "answer_confidence": 0.92,
                    "probabilities": {
                        "Living Room": 0.92,
                        "Kitchen": 0.08,
                    },
                },
            },
        }

        decision = self.client._parse_response(mock_data)

        self.assertIsInstance(decision, LayaDecision)
        self.assertEqual(decision.action.choice, "light.turn_off")
        self.assertAlmostEqual(decision.action.confidence, 0.8143)
        self.assertEqual(decision.target.choice, "Living Room")
        self.assertAlmostEqual(decision.target.confidence, 0.92)

    @patch("aiohttp.ClientSession.get")
    async def test_check_health_ok(self, mock_get):
        """Test check_health when server returns 200 OK."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        result = await self.client.check_health()
        self.assertTrue(result)

    @patch("aiohttp.ClientSession.get")
    async def test_check_health_failure(self, mock_get):
        """Test check_health when server returns non-200."""
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        result = await self.client.check_health()
        self.assertFalse(result)

    @patch("aiohttp.ClientSession.post")
    async def test_decide_success(self, mock_post):
        """Test full decision pipeline with mocked successful server response."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "answers": {
                    "action": {
                        "choice": "turn_on",
                        "answer_confidence": 0.88,
                    },
                    "target": {
                        "choice": "Kitchen",
                        "answer_confidence": 0.95,
                    },
                }
            }
        )
        mock_resp.__aenter__.return_value = mock_resp
        mock_post.return_value = mock_resp

        decision = await self.client.decide(
            command="turn on the kitchen light",
            action_criteria=["turn_on", "turn_off"],
            target_criteria=["Kitchen", "Living Room"],
        )

        self.assertEqual(decision.action.choice, "turn_on")
        self.assertAlmostEqual(decision.action.confidence, 0.88)
        self.assertEqual(decision.target.choice, "Kitchen")
        self.assertAlmostEqual(decision.target.confidence, 0.95)

    @patch("aiohttp.ClientSession.post")
    async def test_decide_auth_error(self, mock_post):
        """Test HTTP 401 raises LayaAuthError."""
        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.__aenter__.return_value = mock_resp
        mock_post.return_value = mock_resp

        with self.assertRaises(LayaAuthError):
            await self.client.decide(
                command="test command",
                action_criteria=["turn_on"],
                target_criteria=["Living Room"],
            )

    @patch("aiohttp.ClientSession.post")
    async def test_decide_api_error(self, mock_post):
        """Test HTTP 422 or 500 raises LayaApiError."""
        mock_resp = MagicMock()
        mock_resp.status = 422
        mock_resp.text = AsyncMock(return_value="Validation Error")
        mock_resp.__aenter__.return_value = mock_resp
        mock_post.return_value = mock_resp

        with self.assertRaises(LayaApiError):
            await self.client.decide(
                command="test command",
                action_criteria=["turn_on"],
                target_criteria=["Living Room"],
            )

    @patch("aiohttp.ClientSession.post")
    async def test_decide_timeout_error(self, mock_post):
        """Test asyncio.TimeoutError is caught and raised as LayaTimeoutError."""
        mock_post.side_effect = asyncio.TimeoutError()

        with self.assertRaises(LayaTimeoutError):
            await self.client.decide(
                command="test command",
                action_criteria=["turn_on"],
                target_criteria=["Living Room"],
            )

    @patch("aiohttp.ClientSession.post")
    async def test_decide_client_network_error(self, mock_post):
        """Test generic aiohttp.ClientError is caught and raised as LayaConnectionError."""
        mock_post.side_effect = aiohttp.ClientError("DNS resolution failed")

        with self.assertRaises(LayaConnectionError):
            await self.client.decide(
                command="test command",
                action_criteria=["turn_on"],
                target_criteria=["Living Room"],
            )

    @patch("aiohttp.ClientSession.get")
    async def test_check_health_timeout(self, mock_get):
        """Test check_health returns False when request times out."""
        mock_get.side_effect = asyncio.TimeoutError()
        result = await self.client.check_health()
        self.assertFalse(result)

    @patch("aiohttp.ClientSession.post")
    async def test_query_generic_questions(self, mock_post):
        """Test generic query method returning multiple arbitrary question answers."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "answers": {
                    "action": {"choice": "turn_on", "confidence": 0.95},
                    "area": {"choice": "Kitchen", "confidence": 0.98},
                }
            }
        )
        mock_resp.__aenter__.return_value = mock_resp
        mock_post.return_value = mock_resp

        results = await self.client.query(
            command="turn on kitchen",
            questions={
                "action": {"type": "choice", "criteria": ["turn_on"]},
                "area": {"type": "choice", "criteria": ["Kitchen", "Living Room"]},
            },
        )

        self.assertIn("action", results)
        self.assertIn("area", results)
        self.assertEqual(results["action"].choice, "turn_on")
        self.assertEqual(results["area"].choice, "Kitchen")
        self.assertAlmostEqual(results["area"].confidence, 0.98)


if __name__ == "__main__":
    unittest.main()
