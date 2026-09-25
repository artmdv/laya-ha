"""Asynchronous client for the Laya System-1 decision engine."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class LayaError(Exception):
    """Base exception for Laya errors."""


class LayaConnectionError(LayaError):
    """Connection failure to Laya server."""


class LayaTimeoutError(LayaError):
    """Timeout waiting for Laya response."""


class LayaAuthError(LayaError):
    """Authentication failure (e.g. invalid API key)."""


class LayaApiError(LayaError):
    """API-level error returned by Laya server."""


@dataclass
class DecisionChoice:
    """Represents a single question's decision from Laya."""

    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass
class LayaDecision:
    """Container for multi-question smart home decisions."""

    action: DecisionChoice
    target: DecisionChoice
    raw_response: dict[str, Any] = field(default_factory=dict)


class LayaClient:
    """Client for interacting with Laya System-1 /v1/systemone server."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 3.0,
        session: aiohttp.ClientSession | None = None,
        debug_logging: bool = False,
    ) -> None:
        """Initialize the Laya client."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else None
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._own_session = False
        self.debug_logging = debug_logging

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close the underlying session if owned by this client."""
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    def _get_headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check_health(self) -> bool:
        """Check if the Laya server is reachable and healthy."""
        session = await self._get_session()
        try:
            async with session.get(
                f"{self.base_url}/health",
                headers=self._get_headers(),
                timeout=self.timeout,
            ) as response:
                if response.status == 200:
                    return True
                _LOGGER.warning("Laya health check returned HTTP %d", response.status)
                return False
        except (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError):
            _LOGGER.debug("Laya health check timed out")
            return False
        except aiohttp.ClientConnectorError as err:
            _LOGGER.debug("Laya health check connector error: %s", err)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.debug("Laya health check client error: %s", err)
            return False
        except Exception as err:
            _LOGGER.debug("Laya health check unexpected error: %s", err)
            return False

    async def query(
        self,
        command: str,
        questions: dict[str, Any],
    ) -> dict[str, DecisionChoice]:
        """Send arbitrary questions to Laya for non-autoregressive neural classification."""
        session = await self._get_session()
        url = f"{self.base_url}/v1/systemone"

        payload = {
            "state": {"command": command},
            "questions": questions,
        }

        if self.debug_logging:
            _LOGGER.warning(
                "Laya [DEBUG RAW REQUEST] POST %s:\n%s",
                url,
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

        try:
            async with session.post(
                url,
                json=payload,
                headers=self._get_headers(),
                timeout=self.timeout,
            ) as response:
                if response.status in (401, 403):
                    raise LayaAuthError(f"Authentication failed with HTTP {response.status}")

                if response.status != 200:
                    error_text = await response.text()
                    if self.debug_logging:
                        _LOGGER.warning(
                            "Laya [DEBUG RAW ERROR RESPONSE] HTTP %d: %s",
                            response.status,
                            error_text,
                        )
                    raise LayaApiError(
                        f"Laya server returned HTTP {response.status}: {error_text}"
                    )

                data = await response.json()
                if self.debug_logging:
                    _LOGGER.warning(
                        "Laya [DEBUG RAW RESPONSE] HTTP %d:\n%s",
                        response.status,
                        json.dumps(data, ensure_ascii=False, indent=2),
                    )

                answers = data.get("answers", {})
                result: dict[str, DecisionChoice] = {}
                for q_name, q_data in answers.items():
                    result[q_name] = DecisionChoice(
                        choice=q_data.get("choice", ""),
                        confidence=float(
                            q_data.get("answer_confidence", q_data.get("confidence", 0.0))
                        ),
                        probabilities=q_data.get("probabilities", {}),
                    )
                return result

        except (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError) as err:
            raise LayaTimeoutError(
                f"Timed out communicating with Laya server after {self.timeout.total}s"
            ) from err
        except aiohttp.ClientConnectorError as err:
            raise LayaConnectionError(
                f"Failed to connect to Laya server at {self.base_url}: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise LayaConnectionError(
                f"Network error communicating with Laya server: {err}"
            ) from err

    async def decide(
        self,
        command: str,
        action_criteria: dict[str, str] | list[str],
        target_criteria: list[str],
    ) -> LayaDecision:
        """Send a natural language voice command to Laya for System-1 classification.

        Args:
            command: The transcribed user sentence (e.g. 'turn off living room light')
            action_criteria: Available actions (keys or dict of action->description)
            target_criteria: Available device/area target names

        Returns:
            LayaDecision with parsed action and target choices
        """
        questions = {
            "action": {
                "type": "choice",
                "instructions": "Which smart home action should be performed?",
                "criteria": action_criteria,
            },
            "target": {
                "type": "choice",
                "instructions": "Which device, room, or entity is targeted?",
                "criteria": target_criteria,
            },
        }
        res = await self.query(command, questions)
        action_choice = res.get(
            "action", DecisionChoice(choice="", confidence=0.0, probabilities={})
        )
        target_choice = res.get(
            "target", DecisionChoice(choice="", confidence=0.0, probabilities={})
        )
        return LayaDecision(action=action_choice, target=target_choice)

    def _parse_response(self, data: dict[str, Any]) -> LayaDecision:
        """Parse the /v1/systemone JSON response into structured dataclasses."""
        answers = data.get("answers", {})

        action_data = answers.get("action", {})
        action_choice = DecisionChoice(
            choice=action_data.get("choice", ""),
            confidence=float(action_data.get("answer_confidence", action_data.get("confidence", 0.0))),
            probabilities=action_data.get("probabilities", {}),
        )

        target_data = answers.get("target", {})
        target_choice = DecisionChoice(
            choice=target_data.get("choice", ""),
            confidence=float(target_data.get("answer_confidence", target_data.get("confidence", 0.0))),
            probabilities=target_data.get("probabilities", {}),
        )

        return LayaDecision(action=action_choice, target=target_choice, raw_response=data)
