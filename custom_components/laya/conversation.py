"""Conversation platform implementation for Laya System-1."""

from __future__ import annotations

import logging
import re
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
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.intent import IntentResponse

from .client import (
    DecisionChoice,
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
    CONF_HIERARCHICAL_ROUTING,
    CONF_RESPONSE_STYLE,
    CONF_TIMEOUT,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_EXPOSED_DOMAINS,
    DEFAULT_HIERARCHICAL_ROUTING,
    DEFAULT_NAME,
    DEFAULT_RESPONSE_STYLE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LOCALIZED_RESPONSES,
    LOCALIZED_STATES,
    MAX_TARGET_CANDIDATES,
    STYLE_VERBOSE,
)

_LOGGER = logging.getLogger(__name__)


def _safe_str(val: Any) -> str:
    """Safely convert any registry alias, name, or ComputedNameType to a clean string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if hasattr(val, "name") and isinstance(val.name, str):
        return val.name.strip()
    return str(val).strip()


def filter_target_candidates(
    text: str,
    target_map: dict[str, dict[str, Any]],
    max_limit: int = MAX_TARGET_CANDIDATES,
) -> list[str]:
    """Intelligently prioritize target candidates to stay safely within Laya head_max_len."""
    if len(target_map) <= max_limit:
        return list(target_map.keys())

    text_lower = text.lower()
    # Word tokens of length >= 2
    tokens = [t for t in re.findall(r"\w+", text_lower) if len(t) >= 2]
    # Word stems (e.g. 4 chars prefix) to match inflected language forms
    stems = [t[:4] for t in tokens if len(t) >= 4]

    scored_targets: list[tuple[float, str]] = []

    for name, meta in target_map.items():
        name_lower = name.lower()
        score = 0.0

        target_type = meta.get("type")
        domain = meta.get("domain", "")

        # 1. Base architectural weights
        if target_type == "area":
            score += 15.0  # Areas are primary room targets
        elif domain in (
            "light",
            "switch",
            "cover",
            "vacuum",
            "climate",
            "fan",
            "lock",
            "media_player",
        ):
            score += 8.0  # Common actionable home devices
        else:
            score += 2.0  # Diagnostic and background sensors

        # 2. Text matching bonuses
        if name_lower in text_lower:
            score += 50.0
        else:
            name_words = re.findall(r"\w+", name_lower)
            for token in tokens:
                for nw in name_words:
                    if token == nw:
                        score += 30.0
                    elif len(token) >= 3 and (token in nw or nw in token):
                        score += 15.0

            for stem in stems:
                if stem in name_lower:
                    score += 10.0

        scored_targets.append((score, name))

    # Sort descending by score, tie-break by name for deterministic order
    scored_targets.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in scored_targets[:max_limit]]


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
        try:
            return await self._async_process_internal(user_input)
        except Exception as err:
            _LOGGER.exception("Unexpected error during Laya conversation processing: %s", err)
            lang = (user_input.language or "en").lower().split("-")[0]
            return self._build_result(user_input, self._get_text(lang, "error"))

    async def _async_process_internal(self, user_input: ConversationInput) -> ConversationResult:
        """Internal processing pipeline for Laya conversation."""
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
        hierarchical_routing: bool = options.get(
            CONF_HIERARCHICAL_ROUTING, DEFAULT_HIERARCHICAL_ROUTING
        )

        # 1. Discover registered areas and entities
        target_map, area_map = self._build_target_catalog(exposed_domains)

        if not target_map:
            _LOGGER.warning("Laya: No exposed entities found for domains: %s", exposed_domains)
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        # Intelligent candidate filtering to stay safely under Laya head_max_len=256
        target_names = self._filter_target_candidates(text, target_map, MAX_TARGET_CANDIDATES)
        if len(target_map) > MAX_TARGET_CANDIDATES:
            _LOGGER.debug(
                "Filtered %d target candidates down to %d for '%s'",
                len(target_map),
                len(target_names),
                text,
            )

        # 2. Build action criteria with clear descriptions for Laya
        action_criteria = {
            action: meta["description"]
            for action, meta in ACTION_DEFINITIONS.items()
        }

        # 3. Query Laya System-1 decision engine (Hierarchical 2-step or direct)
        try:
            if hierarchical_routing and area_map:
                action_choice, target_choice, target_map = (
                    await self._async_process_hierarchical(
                        text=text,
                        action_criteria=action_criteria,
                        target_map=target_map,
                        area_map=area_map,
                        exposed_domains=exposed_domains,
                        confidence_threshold=confidence_threshold,
                    )
                )
            else:
                decision = await self.client.decide(
                    command=text,
                    action_criteria=action_criteria,
                    target_criteria=target_names,
                )
                action_choice = decision.action
                target_choice = decision.target
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
            action_choice.choice,
            action_choice.confidence,
            target_choice.choice,
            target_choice.confidence,
        )

        # 4. Check confidence guardrail
        if (
            action_choice.confidence < confidence_threshold
            or target_choice.confidence < confidence_threshold
        ):
            _LOGGER.info(
                "Laya decision below confidence threshold (%.2f): action=%.2f, target=%.2f",
                confidence_threshold,
                action_choice.confidence,
                target_choice.confidence,
            )
            return self._build_result(user_input, self._get_text(lang, "low_confidence"))

        # 5. Resolve chosen action and target
        action_name = action_choice.choice
        target_name = target_choice.choice
        action_info = ACTION_DEFINITIONS.get(action_name)

        if not action_info:
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        resolved_target = target_map.get(target_name)
        if not resolved_target:
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        # 6. Handle State Queries (temperature, door/gate status, power, etc.)
        if action_name == "query_state":
            if resolved_target["type"] == "entity":
                entity_id = resolved_target["id"]
                state_obj = self.hass.states.get(entity_id)
                if state_obj is None:
                    return self._build_result(user_input, self._get_text(lang, "not_found"))
                speech = self._format_state_query_response(
                    target_name=target_name,
                    state_obj=state_obj,
                    lang=lang,
                )
                return self._build_result(user_input, speech)
            elif resolved_target["type"] == "area":
                area_id = resolved_target["id"]
                speech = self._query_area_state(area_id, target_name, lang)
                return self._build_result(user_input, speech)

        # 7. Execute service call on entity or entire area
        service_full = action_info.get("service")
        if not service_full:
            return self._build_result(user_input, self._get_text(lang, "not_found"))
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

        # 8. Format user response
        speech = self._format_speech_response(
            action_name=action_name,
            target_name=target_name,
            lang=lang,
            style=response_style,
        )
        return self._build_result(user_input, speech)

    async def _async_process_hierarchical(
        self,
        text: str,
        action_criteria: dict[str, str],
        target_map: dict[str, dict[str, Any]],
        area_map: dict[str, str],
        exposed_domains: list[str],
        confidence_threshold: float,
    ) -> tuple[DecisionChoice, DecisionChoice, dict[str, dict[str, Any]]]:
        """Resolve command in 2 steps: identify Area first, then narrow candidates to that Area."""
        name_to_area_id = {clean_name: aid for aid, clean_name in area_map.items()}

        area_choices = {name: f"Room or area: {name}" for name in name_to_area_id}
        area_choices["none"] = "No specific room or whole-home command"

        step1_questions = {
            "action": {
                "type": "choice",
                "instructions": "Which smart home action should be performed?",
                "criteria": action_criteria,
            },
            "area": {
                "type": "choice",
                "instructions": "Which room or area is mentioned or targeted?",
                "criteria": area_choices,
            },
        }

        step1_res = await self.client.query(text, step1_questions)
        action_choice = step1_res.get(
            "action", DecisionChoice(choice="", confidence=0.0, probabilities={})
        )
        area_choice = step1_res.get(
            "area", DecisionChoice(choice="", confidence=0.0, probabilities={})
        )

        _LOGGER.debug(
            "Hierarchical Step 1: action=%s (conf=%.2f), area=%s (conf=%.2f)",
            action_choice.choice,
            action_choice.confidence,
            area_choice.choice,
            area_choice.confidence,
        )

        # Check if an area was identified with reasonable confidence
        if (
            area_choice.choice
            and area_choice.choice != "none"
            and area_choice.choice in name_to_area_id
            and area_choice.confidence >= confidence_threshold
        ):
            chosen_area_name = area_choice.choice
            chosen_area_id = name_to_area_id[chosen_area_name]

            # Gather entities in this area
            area_entities = self._get_entities_in_area(chosen_area_id, exposed_domains)
            # Include the area itself as a target (e.g. for whole-room actions like 'turn off kitchen')
            area_entities[chosen_area_name] = {
                "type": "area",
                "id": chosen_area_id,
                "domain": "area",
            }

            if len(area_entities) > 1:
                # Step 2: query Laya with only this area's targets
                step2_questions = {
                    "target": {
                        "type": "choice",
                        "instructions": f"Which specific device or target in {chosen_area_name} is targeted?",
                        "criteria": list(area_entities.keys()),
                    }
                }
                step2_res = await self.client.query(text, step2_questions)
                target_choice = step2_res.get(
                    "target", DecisionChoice(choice="", confidence=0.0, probabilities={})
                )
                _LOGGER.debug(
                    "Hierarchical Step 2 in '%s': target=%s (conf=%.2f)",
                    chosen_area_name,
                    target_choice.choice,
                    target_choice.confidence,
                )
                return action_choice, target_choice, area_entities
            elif len(area_entities) == 1:
                single_target = list(area_entities.keys())[0]
                return (
                    action_choice,
                    DecisionChoice(
                        choice=single_target,
                        confidence=area_choice.confidence,
                        probabilities={},
                    ),
                    area_entities,
                )

        # Fallback to direct candidate list if area is 'none' or confidence is low
        fallback_targets = self._filter_target_candidates(text, target_map, MAX_TARGET_CANDIDATES)
        fallback_questions = {
            "target": {
                "type": "choice",
                "instructions": "Which device, room, or entity is targeted?",
                "criteria": fallback_targets,
            }
        }
        step2_res = await self.client.query(text, fallback_questions)
        target_choice = step2_res.get(
            "target", DecisionChoice(choice="", confidence=0.0, probabilities={})
        )
        return action_choice, target_choice, target_map

    def _get_entities_in_area(
        self, area_id: str, exposed_domains: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return all exposed entities belonging to a specific area."""
        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)

        dev_area_map: dict[str, str] = {}
        if dev_reg and hasattr(dev_reg, "devices"):
            for dev in dev_reg.devices.values():
                if getattr(dev, "area_id", None):
                    dev_area_map[dev.id] = dev.area_id

        area_entities: dict[str, dict[str, Any]] = {}

        for state in self.hass.states.async_all():
            if state.domain not in exposed_domains:
                continue

            ent_entry = ent_reg.async_get(state.entity_id) if ent_reg else None
            ent_area_id = None
            if ent_entry:
                ent_area_id = getattr(ent_entry, "area_id", None) or (
                    dev_area_map.get(getattr(ent_entry, "device_id", None))
                    if getattr(ent_entry, "device_id", None)
                    else None
                )

            if ent_area_id == area_id:
                friendly_name = state.attributes.get("friendly_name")
                name_to_use = _safe_str(friendly_name) or state.entity_id
                area_entities[name_to_use] = {
                    "type": "entity",
                    "id": state.entity_id,
                    "domain": state.domain,
                }
                if ent_entry and hasattr(ent_entry, "aliases") and ent_entry.aliases:
                    for alias in ent_entry.aliases:
                        alias_str = _safe_str(alias)
                        if alias_str:
                            area_entities[alias_str] = {
                                "type": "entity",
                                "id": state.entity_id,
                                "domain": state.domain,
                            }

        return area_entities

    @staticmethod
    def _filter_target_candidates(
        text: str,
        target_map: dict[str, dict[str, Any]],
        max_limit: int = MAX_TARGET_CANDIDATES,
    ) -> list[str]:
        """Intelligently prioritize target candidates to stay safely within Laya head_max_len."""
        return filter_target_candidates(text, target_map, max_limit)

    def _build_target_catalog(
        self, exposed_domains: list[str]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """Collect all matching device friendly names and registered areas."""
        target_map: dict[str, dict[str, Any]] = {}
        area_map: dict[str, str] = {}

        # 1. Add Areas (Rooms)
        area_reg = area_registry.async_get(self.hass)
        areas = []
        if hasattr(area_reg, "areas"):
            areas = list(area_reg.areas.values())
        elif hasattr(area_reg, "async_list_areas"):
            areas = area_reg.async_list_areas()

        for area in areas:
            area_name_clean = _safe_str(area.name)
            if area_name_clean:
                area_map[area.id] = area_name_clean
                target_map[area_name_clean] = {"type": "area", "id": area.id, "domain": "area"}

            # Also register area aliases if present
            if hasattr(area, "aliases") and area.aliases:
                for alias in area.aliases:
                    alias_str = _safe_str(alias)
                    if alias_str:
                        target_map[alias_str] = {"type": "area", "id": area.id, "domain": "area"}

        # 2. Add Entities
        ent_reg = entity_registry.async_get(self.hass)
        for state in self.hass.states.async_all():
            domain = state.domain
            if domain not in exposed_domains:
                continue

            entity_id = state.entity_id
            friendly_name = state.attributes.get("friendly_name")
            name_to_use = _safe_str(friendly_name) or entity_id

            target_map[name_to_use] = {"type": "entity", "id": entity_id, "domain": domain}

            # Check entity registry for extra aliases (safely handles ComputedNameType)
            ent_entry = ent_reg.async_get(entity_id)
            if ent_entry and hasattr(ent_entry, "aliases") and ent_entry.aliases:
                for alias in ent_entry.aliases:
                    alias_str = _safe_str(alias)
                    if alias_str:
                        target_map[alias_str] = {"type": "entity", "id": entity_id, "domain": domain}

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

    def _format_state_query_response(
        self, target_name: str, state_obj: Any, lang: str
    ) -> str:
        """Format a clear, natural status query answer for sensors and devices."""
        raw_state = state_obj.state
        unit = state_obj.attributes.get("unit_of_measurement")

        state_dict = LOCALIZED_STATES.get(lang, LOCALIZED_STATES["en"])
        is_word = state_dict.get("is", "is")

        # Handle numeric / measurement states (e.g. 21.5 °C, 55%, 150 W)
        if unit:
            return f"{target_name} {is_word} {raw_state} {unit}"

        # Handle discrete states (open/closed, on/off, locked/unlocked)
        translated_state = state_dict.get(raw_state.lower(), raw_state)
        return f"{target_name} {is_word} {translated_state}"

    def _query_area_state(self, area_id: str, area_name: str, lang: str) -> str:
        """Find the most relevant sensor (temperature/climate) for an area query."""
        state_dict = LOCALIZED_STATES.get(lang, LOCALIZED_STATES["en"])
        is_word = state_dict.get("is", "is")

        # Search for temperature sensor or climate entity in this area
        ent_reg = entity_registry.async_get(self.hass)
        for state in self.hass.states.async_all():
            ent_entry = ent_reg.async_get(state.entity_id)
            if ent_entry and ent_entry.area_id == area_id:
                if state.domain == "climate" and "current_temperature" in state.attributes:
                    temp = state.attributes["current_temperature"]
                    unit = getattr(getattr(self.hass.config, "units", None), "temperature_unit", "°C")
                    return f"{area_name} {is_word} {temp} {unit}"
                if state.domain == "sensor" and state.attributes.get("device_class") == "temperature":
                    unit = state.attributes.get("unit_of_measurement", "°C")
                    return f"{area_name} {is_word} {state.state} {unit}"

        return self._get_text(lang, "not_found")

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
