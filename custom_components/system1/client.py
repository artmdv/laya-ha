"""Asynchronous client for System-1 decision engines (Clef, Laya, Jev, and /v1/systemone)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import DEFAULT_MODEL, DEFAULT_TIMEOUT, ENGINE_CLEF, ENGINE_LAYA

_LOGGER = logging.getLogger(__name__)


class System1Error(Exception):
    """Base exception for System-1 errors."""


class System1ConnectionError(System1Error):
    """Connection failure to System-1 server."""


class System1TimeoutError(System1Error):
    """Timeout waiting for System-1 response."""


class System1AuthError(System1Error):
    """Authentication failure (e.g. invalid API key)."""


class System1ApiError(System1Error):
    """API-level error returned by System-1 server."""


# Backwards-compatibility aliases
LayaError = System1Error
LayaConnectionError = System1ConnectionError
LayaTimeoutError = System1TimeoutError
LayaAuthError = System1AuthError
LayaApiError = System1ApiError


@dataclass
class DecisionChoice:
    """Represents a single question's decision from a System-1 model."""

    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass
class System1Decision:
    """Container for multi-question smart home decisions."""

    action: DecisionChoice
    target: DecisionChoice
    raw_response: dict[str, Any] = field(default_factory=dict)


# Backwards-compatibility alias
LayaDecision = System1Decision


class System1Client:
    """Universal client for interacting with System-1 /v1/systemone decision servers."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        engine: str = ENGINE_CLEF,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        session: aiohttp.ClientSession | None = None,
        debug_logging: bool = False,
    ) -> None:
        """Initialize the System-1 client."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else None
        self.engine = engine
        self.model = model.strip() if model else ""
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
        """Check if the System-1 server is reachable and healthy across Ollama, vLLM, and laya-serve."""
        session = await self._get_session()
        headers = self._get_headers()

        # Try standard health endpoint first
        endpoints = ["/health", "/v1/models", "/"]
        for ep in endpoints:
            try:
                async with session.get(
                    f"{self.base_url}{ep}",
                    headers=headers,
                    timeout=self.timeout,
                ) as response:
                    if response.status in (200, 204):
                        return True
                    if response.status not in (404, 405):
                        _LOGGER.debug("System-1 health probe on %s returned HTTP %d", ep, response.status)
            except (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError):
                _LOGGER.debug("System-1 health check timed out on %s", ep)
            except aiohttp.ClientError as err:
                _LOGGER.debug("System-1 health check error on %s: %s", ep, err)
            except Exception as err:
                _LOGGER.debug("System-1 unexpected error probing %s: %s", ep, err)

        # Fallback probe: send a minimal dummy query to /v1/systemone
        try:
            test_res = await self.query(
                command="ping",
                questions={
                    "ping": {
                        "type": "choice",
                        "instructions": "ping",
                        "criteria": ["pong"],
                    }
                },
            )
            return bool(test_res)
        except Exception as err:
            _LOGGER.debug("System-1 probe via /v1/systemone failed: %s", err)
            return False

    async def query(
        self,
        command: str,
        questions: dict[str, Any],
    ) -> dict[str, DecisionChoice]:
        """Send arbitrary questions to System-1 for non-autoregressive neural classification."""
        session = await self._get_session()
        url = f"{self.base_url}/v1/systemone"

        # Format state based on engine (Clef accepts string or dict, Laya accepts dict)
        state_payload: Any = {"command": command} if self.engine == ENGINE_LAYA else command

        payload: dict[str, Any] = {
            "state": state_payload,
            "questions": questions,
        }
        if self.model:
            payload["model"] = self.model

        if self.debug_logging:
            _LOGGER.warning(
                "System-1 [DEBUG RAW REQUEST] (%s) POST %s:\n%s",
                self.engine,
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
                    raise System1AuthError(f"Authentication failed with HTTP {response.status}")

                # If server complains about string state format, auto-retry with dict format
                if response.status == 400 and not isinstance(state_payload, dict):
                    payload["state"] = {"command": command}
                    async with session.post(
                        url,
                        json=payload,
                        headers=self._get_headers(),
                        timeout=self.timeout,
                    ) as retry_resp:
                        if retry_resp.status == 200:
                            response = retry_resp

                if response.status != 200:
                    error_text = await response.text()
                    if self.debug_logging:
                        _LOGGER.warning(
                            "System-1 [DEBUG RAW ERROR RESPONSE] HTTP %d: %s",
                            response.status,
                            error_text,
                        )
                    raise System1ApiError(
                        f"System-1 server returned HTTP {response.status}: {error_text}"
                    )

                data = await response.json()
                if self.debug_logging:
                    _LOGGER.warning(
                        "System-1 [DEBUG RAW RESPONSE] HTTP %d:\n%s",
                        response.status,
                        json.dumps(data, ensure_ascii=False, indent=2),
                    )

                # Normalize answers from direct System-1 schema or nested result wrapper
                answers = data.get("answers") or data.get("result", {}).get("answers", {})
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
            raise System1TimeoutError(
                f"Timed out communicating with System-1 server after {self.timeout.total}s"
            ) from err
        except aiohttp.ClientConnectorError as err:
            raise System1ConnectionError(
                f"Failed to connect to System-1 server at {self.base_url}: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise System1ConnectionError(
                f"Network error communicating with System-1 server: {err}"
            ) from err

    async def decide(
        self,
        command: str,
        action_criteria: dict[str, str] | list[str],
        target_criteria: list[str] | dict[str, str],
    ) -> System1Decision:
        """Send a natural language voice command to System-1 for classification.

        Args:
            command: The transcribed user sentence (e.g. 'turn off living room light')
            action_criteria: Available actions (keys or dict of action->description)
            target_criteria: Available device/area target names or dict of target->description

        Returns:
            System1Decision with parsed action and target choices
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
        return System1Decision(action=action_choice, target=target_choice)

    def _parse_response(self, data: dict[str, Any]) -> System1Decision:
        """Parse the /v1/systemone JSON response into structured dataclasses."""
        answers = data.get("answers") or data.get("result", {}).get("answers", {})

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

        return System1Decision(action=action_choice, target=target_choice, raw_response=data)


# Backwards-compatibility alias
LayaClient = System1Client
