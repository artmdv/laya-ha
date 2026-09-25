"""Conversation platform implementation for Laya System-1."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    ConversationEntity,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, entity_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.intent import IntentResponse

from .client import (
    LayaAuthError,
    LayaClient,
    LayaConnectionError,
    LayaError,
    LayaTimeoutError,
)
from .const import (
    ACTION_DEFINITIONS,
    CONF_API_KEY,
    CONF_CONFIDENCE_THRESHOLD,
    CONF_EXPOSED_DOMAINS,
    CONF_RESPONSE_STYLE,
    CONF_TIMEOUT,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_EXPOSED_DOMAINS,
    DEFAULT_NAME,
    DEFAULT_RESPONSE_STYLE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LOCALIZED_RESPONSES,
    STYLE_VERBOSE,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Laya conversation entity from a config entry."""
    client: LayaClient = hass.data[DOMAIN][entry.entry_id]["client"]
    async_add_entities([LayaConversationEntity(hass, entry, client)])


class LayaConversationEntity(ConversationEntity):
    """Conversation entity powered by Laya System-1 non-autoregressive decision engine."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: LayaClient,
    ) -> None:
        """Initialize the conversation entity."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self._attr_name = DEFAULT_NAME
        self._attr_unique_id = f"{entry.entry_id}_conversation"

        # Explicitly declare device control support for Home Assistant 2024.8+
        if hasattr(conversation, "ConversationEntityFeature") and hasattr(
            conversation.ConversationEntityFeature, "CONTROL"
        ):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    def supported_languages(self) -> list[str] | str:
        """Return supported languages. Laya multilingual checkpoint supports all languages."""
        return MATCH_ALL

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a spoken or written natural language command using Laya."""
        text = user_input.text.strip()
        lang = (user_input.language or "en").lower().split("-")[0]
        options = self.entry.options

        confidence_threshold: float = options.get(
            CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD
        )
        exposed_domains: list[str] = options.get(
            CONF_EXPOSED_DOMAINS, DEFAULT_EXPOSED_DOMAINS
        )
        response_style: str = options.get(CONF_RESPONSE_STYLE, DEFAULT_RESPONSE_STYLE)

        # 1. Discover registered areas and entities
        target_map, area_map = self._build_target_catalog(exposed_domains)

        target_names = list(target_map.keys())
        if not target_names:
            _LOGGER.warning("Laya: No exposed entities found for domains: %s", exposed_domains)
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        # 2. Build action criteria with clear descriptions for Laya
        action_criteria = {
            action: meta["description"]
            for action, meta in ACTION_DEFINITIONS.items()
        }

        # 3. Query Laya System-1 decision engine
        try:
            decision = await self.client.decide(
                command=text,
                action_criteria=action_criteria,
                target_criteria=target_names,
            )
        except LayaConnectionError as err:
            _LOGGER.error("Laya connection failed: %s", err)
            return self._build_result(user_input, self._get_text(lang, "error"))
        except LayaTimeoutError:
            _LOGGER.error("Laya request timed out")
            return self._build_result(user_input, self._get_text(lang, "error"))
        except LayaAuthError:
            _LOGGER.error("Laya authentication failed - check your configured API key")
            return self._build_result(user_input, self._get_text(lang, "error"))
        except LayaError as err:
            _LOGGER.error("Laya execution error: %s", err)
            return self._build_result(user_input, self._get_text(lang, "error"))

        _LOGGER.debug(
            "Laya decision for '%s': action=%s (conf=%.2f), target=%s (conf=%.2f)",
            text,
            decision.action.choice,
            decision.action.confidence,
            decision.target.choice,
            decision.target.confidence,
        )

        # 4. Check confidence guardrail
        if (
            decision.action.confidence < confidence_threshold
            or decision.target.confidence < confidence_threshold
        ):
            _LOGGER.info(
                "Laya decision below confidence threshold (%.2f): action=%.2f, target=%.2f",
                confidence_threshold,
                decision.action.confidence,
                decision.target.confidence,
            )
            return self._build_result(user_input, self._get_text(lang, "low_confidence"))

        # 5. Resolve chosen action and target
        action_name = decision.action.choice
        target_name = decision.target.choice
        action_info = ACTION_DEFINITIONS.get(action_name)

        if not action_info:
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        resolved_target = target_map.get(target_name)
        if not resolved_target:
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        # 6. Execute service call on entity or entire area
        service_full = action_info["service"]
        service_domain, service_name = service_full.split(".", 1)

        try:
            if resolved_target["type"] == "area":
                area_id = resolved_target["id"]
                _LOGGER.info(
                    "Executing %s on area '%s' (%s)",
                    service_full,
                    target_name,
                    area_id,
                )
                await self.hass.services.async_call(
                    service_domain,
                    service_name,
                    {"area_id": area_id},
                    blocking=True,
                )
            else:
                entity_id = resolved_target["id"]
                _LOGGER.info(
                    "Executing %s on entity '%s' (%s)",
                    service_full,
                    target_name,
                    entity_id,
                )
                await self.hass.services.async_call(
                    service_domain,
                    service_name,
                    {"entity_id": entity_id},
                    blocking=True,
                )
        except Exception as err:
            _LOGGER.error("Failed to execute service %s: %s", service_full, err)
            return self._build_result(user_input, self._get_text(lang, "error"))

        # 7. Format user response
        speech = self._format_speech_response(
            action_name=action_name,
            target_name=target_name,
            lang=lang,
            style=response_style,
        )
        return self._build_result(user_input, speech)

    def _build_target_catalog(
        self, exposed_domains: list[str]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """Collect all matching device friendly names and registered areas."""
        target_map: dict[str, dict[str, Any]] = {}
        area_map: dict[str, str] = {}

        # 1. Add Areas (Rooms)
        area_reg = area_registry.async_get(self.hass)
        for area in area_reg.async_list_areas():
            area_name_clean = area.name.strip()
            area_map[area.id] = area_name_clean
            target_map[area_name_clean] = {"type": "area", "id": area.id}
            # Also register area aliases if present
            if hasattr(area, "aliases") and area.aliases:
                for alias in area.aliases:
                    target_map[alias.strip()] = {"type": "area", "id": area.id}

        # 2. Add Entities
        ent_reg = entity_registry.async_get(self.hass)
        for state in self.hass.states.async_all():
            domain = state.domain
            if domain not in exposed_domains:
                continue

            entity_id = state.entity_id
            friendly_name = state.attributes.get("friendly_name")
            name_to_use = (friendly_name or entity_id).strip()

            target_map[name_to_use] = {"type": "entity", "id": entity_id}

            # Check entity registry for extra aliases
            ent_entry = ent_reg.async_get(entity_id)
            if ent_entry and ent_entry.aliases:
                for alias in ent_entry.aliases:
                    target_map[alias.strip()] = {"type": "entity", "id": entity_id}

        return target_map, area_map

    def _format_speech_response(
        self, action_name: str, target_name: str, lang: str, style: str
    ) -> str:
        """Format the spoken feedback based on language and configured style."""
        base_action_text = self._get_text(lang, action_name)

        if style == STYLE_VERBOSE:
            if lang == "lt":
                return f"{base_action_text} ({target_name})"
            return f"{base_action_text} {target_name}"

        return base_action_text

    def _get_text(self, lang: str, key: str) -> str:
        """Get localized phrase with fallback to English."""
        lang_dict = LOCALIZED_RESPONSES.get(lang, LOCALIZED_RESPONSES["en"])
        return lang_dict.get(key, LOCALIZED_RESPONSES["en"].get(key, "Done"))

    def _build_result(
        self, user_input: ConversationInput, speech_text: str
    ) -> ConversationResult:
        """Construct a standardized Home Assistant ConversationResult."""
        intent_response = IntentResponse(language=user_input.language)
        intent_response.async_set_speech(speech_text)
        return ConversationResult(
            response=intent_response,
            conversation_id=user_input.conversation_id,
        )
