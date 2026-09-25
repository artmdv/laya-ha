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
    ACTION_COMPATIBLE_DOMAINS,
    ACTION_DEFINITIONS,
    AREA_SYNONYMS,
    CONF_API_KEY,
    CONF_CONFIDENCE_THRESHOLD,
    CONF_DEBUG_LOGGING,
    CONF_EXPOSED_DOMAINS,
    CONF_HIERARCHICAL_ROUTING,
    CONF_RESPONSE_STYLE,
    CONF_TIMEOUT,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_DEBUG_LOGGING,
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
        debug_logging: bool = options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
        self.client.debug_logging = debug_logging

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
        resolved_area = None
        try:
            if hierarchical_routing and area_map:
                action_choice, target_choice, target_map, resolved_area = (
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

        if debug_logging:
            _LOGGER.warning(
                "Laya [DEBUG] Evaluated decision for '%s': action='%s' (conf=%.3f), target='%s' (conf=%.3f), area='%s', threshold=%.2f",
                text,
                action_choice.choice,
                action_choice.confidence,
                target_choice.choice,
                target_choice.confidence,
                resolved_area or "none",
                confidence_threshold,
            )
        else:
            _LOGGER.debug(
                "Laya decision for '%s': action=%s (conf=%.2f), target=%s (conf=%.2f), area=%s",
                text,
                action_choice.choice,
                action_choice.confidence,
                target_choice.choice,
                target_choice.confidence,
                resolved_area or "none",
            )
        # Interrogative check: 'ar ...' or trailing '?' indicates an inquiry, never a command
        text_lower = text.lower()
        is_question = (
            text_lower.startswith("ar ")
            or text.strip().endswith("?")
            or text_lower.startswith("is ")
            or text_lower.startswith("what ")
            or text_lower.startswith("kokia ")
            or text_lower.startswith("koks ")
            or text_lower.startswith("kiek ")
        )
        if is_question and action_choice.choice in (
            "turn_on",
            "turn_off",
            "toggle",
            "open_cover",
            "close_cover",
        ):
            _LOGGER.debug(
                "Overriding action '%s' to 'query_state' due to interrogative question syntax",
                action_choice.choice,
            )
            action_choice = DecisionChoice(
                choice="query_state",
                confidence=max(action_choice.confidence, 0.90),
                probabilities=action_choice.probabilities,
            )

        debug_lines = []
        if resolved_area:
            debug_lines.append(f"Area: {resolved_area}")
        debug_lines.append(f"Action: {action_choice.choice} (conf: {action_choice.confidence:.2f})")
        debug_lines.append(f"Target: {target_choice.choice} (conf: {target_choice.confidence:.2f})")
        debug_lines.append(f"Threshold: {confidence_threshold:.2f}")
        debug_card_base = "\n".join(debug_lines) if debug_logging else None

        # 4. Check confidence guardrail
        if (
            action_choice.confidence < confidence_threshold
            or target_choice.confidence < confidence_threshold
        ):
            if debug_logging:
                _LOGGER.warning(
                    "Laya [DEBUG] Rejected command due to low confidence: action_conf=%.3f, target_conf=%.3f < threshold=%.2f",
                    action_choice.confidence,
                    target_choice.confidence,
                    confidence_threshold,
                )
            else:
                _LOGGER.info(
                    "Laya decision below confidence threshold (%.2f): action=%.2f, target=%.2f",
                    confidence_threshold,
                    action_choice.confidence,
                    target_choice.confidence,
                )
            debug_card = f"{debug_card_base}\nStatus: Low confidence rejected" if debug_card_base else None
            return self._build_result(user_input, self._get_text(lang, "low_confidence"), debug_card=debug_card)

        # 5. Resolve chosen action and target
        action_name = action_choice.choice
        target_name = target_choice.choice
        action_info = ACTION_DEFINITIONS.get(action_name)

        if not action_info or action_name == "no_action":
            debug_card = f"{debug_card_base}\nStatus: Classified as unhandled / non-action" if debug_card_base else None
            return self._build_result(user_input, self._get_text(lang, "no_action"), debug_card=debug_card)

        resolved_target = target_map.get(target_name)
        if not resolved_target:
            debug_card = f"{debug_card_base}\nStatus: Target not found in catalog ({target_name})" if debug_card_base else None
            return self._build_result(user_input, self._get_text(lang, "not_found"), debug_card=debug_card)

        # Validate domain compatibility (e.g. avoid vacuum.start on a switch entity)
        compatible_domains = ACTION_COMPATIBLE_DOMAINS.get(action_name)
        if (
            compatible_domains is not None
            and resolved_target.get("type") == "entity"
            and resolved_target.get("domain") not in compatible_domains
        ):
            if debug_logging:
                _LOGGER.warning(
                    "Laya [DEBUG] Rejected command due to domain mismatch: action '%s' requires %s, but target '%s' has domain '%s'",
                    action_name,
                    compatible_domains,
                    target_name,
                    resolved_target.get("domain"),
                )
            debug_card = (
                f"{debug_card_base}\nTarget ID: {resolved_target.get('id')}\nStatus: Domain mismatch (action '{action_name}' incompatible with domain '{resolved_target.get('domain')}')"
                if debug_card_base
                else None
            )
            return self._build_result(
                user_input,
                self._get_text(lang, "low_confidence"),
                resolved_target,
                debug_card=debug_card,
            )

        # 6. Handle State Queries (temperature, door/gate status, power, etc.)
        if action_name == "query_state":
            if resolved_target["type"] == "entity":
                entity_id = resolved_target["id"]
                state_obj = self.hass.states.get(entity_id)
                if state_obj is None:
                    debug_card = f"{debug_card_base}\nTarget ID: {entity_id}\nStatus: State object missing" if debug_card_base else None
                    return self._build_result(user_input, self._get_text(lang, "not_found"), resolved_target, debug_card=debug_card)
                speech = self._format_state_query_response(
                    target_name=target_name,
                    state_obj=state_obj,
                    lang=lang,
                )
                debug_card = f"{debug_card_base}\nTarget ID: {entity_id}\nQuery: {speech}" if debug_card_base else None
                return self._build_result(user_input, speech, resolved_target, debug_card=debug_card)
            elif resolved_target["type"] == "area":
                area_id = resolved_target["id"]
                speech = self._query_area_state(area_id, target_name, lang)
                debug_card = f"{debug_card_base}\nArea ID: {area_id}\nQuery: {speech}" if debug_card_base else None
                return self._build_result(user_input, speech, resolved_target, debug_card=debug_card)

        # 7. Execute service call on entity or entire area
        service_full = action_info.get("service")
        if not service_full:
            debug_card = f"{debug_card_base}\nStatus: Service not mapped for {action_name}" if debug_card_base else None
            return self._build_result(user_input, self._get_text(lang, "not_found"), resolved_target, debug_card=debug_card)
        service_domain, service_name = service_full.split(".", 1)

        try:
            if resolved_target["type"] == "area":
                area_id = resolved_target["id"]
                # Determine safe target domain for area command to prevent turning on appliances/dishwashers
                target_domain = "light"  # Default safe domain for room commands
                text_lower = text.lower()
                if any(w in text_lower for w in ("fan", "ventiliat", "vėdinim")):
                    target_domain = "fan"
                elif any(w in text_lower for w in ("cover", "užuolaid", "rolet", "žaliuz", "blind", "curtain", "shutter")):
                    target_domain = "cover"
                elif any(w in text_lower for w in ("switch", "socket", "rozet", "kištuk", "plug")):
                    target_domain = "switch"
                elif any(w in text_lower for w in ("vacuum", "siurbl")):
                    target_domain = "vacuum"
                elif any(w in text_lower for w in ("media", "muzik", "grotuv", "televizor", "tv", "player")):
                    target_domain = "media_player"

                if service_domain == "homeassistant":
                    target_service = f"{target_domain}.{service_name}"
                else:
                    target_service = service_full

                srv_domain, srv_name = target_service.split(".", 1)
                if (
                    hasattr(self.hass, "services")
                    and hasattr(self.hass.services, "has_service")
                    and not self.hass.services.has_service(srv_domain, srv_name)
                ):
                    if self.hass.services.has_service("light", service_name):
                        srv_domain, srv_name = "light", service_name
                    else:
                        srv_domain, srv_name = service_domain, service_name

                log_msg = f"Executing {srv_domain}.{srv_name} on area '{target_name}' ({area_id})"
                if debug_logging:
                    _LOGGER.warning("Laya [DEBUG] %s", log_msg)
                else:
                    _LOGGER.info(log_msg)

                await self.hass.services.async_call(
                    srv_domain,
                    srv_name,
                    service_data={},
                    target={"area_id": area_id},
                    blocking=True,
                )
            else:
                entity_id = resolved_target["id"]
                log_msg = f"Executing {service_full} on entity '{target_name}' ({entity_id})"
                if debug_logging:
                    _LOGGER.warning("Laya [DEBUG] %s", log_msg)
                else:
                    _LOGGER.info(log_msg)

                await self.hass.services.async_call(
                    service_domain,
                    service_name,
                    service_data={"entity_id": entity_id},
                    target={"entity_id": entity_id},
                    blocking=True,
                )
        except Exception as err:
            _LOGGER.error("Failed to execute service %s: %s", service_full, err)
            debug_card = f"{debug_card_base}\nExecuted: {service_full}\nError: {err}" if debug_card_base else None
            return self._build_result(user_input, self._get_text(lang, "error"), resolved_target, debug_card=debug_card)

        # Append execution summary to debug card if present
        if debug_card_base:
            executed_srv = f"{srv_domain}.{srv_name}" if resolved_target["type"] == "area" else service_full
            debug_card = f"{debug_card_base}\nTarget ID: {resolved_target.get('id')}\nExecuted: {executed_srv}"
        else:
            debug_card = None

        # 8. Format user response
        speech = self._format_speech_response(
            action_name=action_name,
            target_name=target_name,
            lang=lang,
            style=response_style,
        )
        debug_card = f"{debug_card_base}\nTarget ID: {resolved_target.get('id')}\nExecuted: {service_full}" if debug_card_base else None
        return self._build_result(user_input, speech, resolved_target, debug_card=debug_card)

    async def _async_process_hierarchical(
        self,
        text: str,
        action_criteria: dict[str, str],
        target_map: dict[str, dict[str, Any]],
        area_map: dict[str, str],
        exposed_domains: list[str],
        confidence_threshold: float,
    ) -> tuple[DecisionChoice, DecisionChoice, dict[str, dict[str, Any]], str | None]:
        """Resolve command in 2 steps: identify Area first, then narrow candidates to that Area."""
        name_to_area_id = {clean_name: aid for aid, clean_name in area_map.items()}

        area_choices = {name: f"Room or area: {name} (zona ar kambarys {name})" for name in name_to_area_id}
        area_choices["none"] = "No specific room or whole-home command (nėra konkretaus kambario ar zonos)"

        step1_questions = {
            "action": {
                "type": "choice",
                "instructions": "Which smart home action should be performed? (Kuris veiksmas turi būti atliktas?)",
                "criteria": action_criteria,
            },
            "area": {
                "type": "choice",
                "instructions": "Which room or area is mentioned or targeted? (Kuris kambarys ar zona paminėta komandoje?)",
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

        # Check if an area is directly mentioned in the command or identified by Laya
        text_lower = text.lower()
        direct_matched_area = None
        for clean_name in name_to_area_id:
            c_lower = clean_name.lower()
            stem = c_lower[:4] if len(c_lower) >= 4 else c_lower
            if stem in text_lower or c_lower in text_lower:
                direct_matched_area = clean_name
                break

            # Multilingual synonym match (e.g. English area 'Kitchen' matched from Lithuanian 'virtuvėj')
            matched_synonym = False
            for syn_key, syn_list in AREA_SYNONYMS.items():
                if syn_key in c_lower or any(s in c_lower for s in syn_list if len(s) >= 4):
                    if any(syn in text_lower for syn in syn_list):
                        direct_matched_area = clean_name
                        matched_synonym = True
                        break
            if matched_synonym:
                break

        chosen_area_name = None
        if direct_matched_area:
            chosen_area_name = direct_matched_area
        elif (
            area_choice.choice
            and area_choice.choice != "none"
            and area_choice.choice in name_to_area_id
            and area_choice.confidence >= 0.25
        ):
            chosen_area_name = area_choice.choice

        if chosen_area_name:
            chosen_area_id = name_to_area_id[chosen_area_name]

            # Gather entities in this area
            area_entities = self._get_entities_in_area(chosen_area_id, exposed_domains)
            # Include the area itself as a target (e.g. for whole-room actions like 'turn off kitchen')
            area_entities[chosen_area_name] = {
                "type": "area",
                "id": chosen_area_id,
                "domain": "area",
            }
            # Also register multilingual aliases of this room so Laya matches them naturally
            for syn_key, syn_list in AREA_SYNONYMS.items():
                if syn_key in chosen_area_name.lower() or any(s in chosen_area_name.lower() for s in syn_list if len(s) >= 4):
                    for syn in syn_list:
                        if syn in text_lower and syn not in area_entities:
                            area_entities[syn] = {
                                "type": "area",
                                "id": chosen_area_id,
                                "domain": "area",
                            }
                            break

            if len(area_entities) > 1:
                # Step 2: query Laya with only this area's targets
                step2_questions = {
                    "target": {
                        "type": "choice",
                        "instructions": f"Which specific device or target in {chosen_area_name} is targeted? (Kuris įrenginys {chosen_area_name} zonoje?)",
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
                return action_choice, target_choice, area_entities, chosen_area_name
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
                    chosen_area_name,
                )

        # Fallback to direct candidate list if area is 'none' or confidence is low
        fallback_targets = self._filter_target_candidates(text, target_map, MAX_TARGET_CANDIDATES)
        fallback_questions = {
            "target": {
                "type": "choice",
                "instructions": "Which device, room, or entity is targeted? (Kuris įrenginys ar zona?)",
                "criteria": fallback_targets,
            }
        }
        step2_res = await self.client.query(text, fallback_questions)
        target_choice = step2_res.get(
            "target", DecisionChoice(choice="", confidence=0.0, probabilities={})
        )
        return action_choice, target_choice, target_map, None

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

        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)

        dev_area_map: dict[str, str] = {}
        if dev_reg and hasattr(dev_reg, "devices"):
            for dev in dev_reg.devices.values():
                if getattr(dev, "area_id", None):
                    dev_area_map[dev.id] = dev.area_id

        # Search for temperature sensor or climate entity in this area
        found_sensor = None
        for state in self.hass.states.async_all():
            if state.state in (None, "unavailable", "unknown"):
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
                if state.domain == "climate" and "current_temperature" in state.attributes:
                    temp = state.attributes["current_temperature"]
                    unit = getattr(getattr(self.hass.config, "units", None), "temperature_unit", "°C")
                    return f"{area_name} {is_word} {temp} {unit}"
                if state.domain == "sensor" and (
                    state.attributes.get("device_class") == "temperature"
                    or "temperature" in state.entity_id.lower()
                    or "temperat" in state.entity_id.lower()
                    or state.attributes.get("unit_of_measurement") in ("°C", "°F", "K")
                ):
                    unit = state.attributes.get("unit_of_measurement", "°C")
                    return f"{area_name} {is_word} {state.state} {unit}"
                if state.domain == "sensor" and not found_sensor:
                    found_sensor = state

        if found_sensor:
            unit = found_sensor.attributes.get("unit_of_measurement", "")
            return f"{area_name} {is_word} {found_sensor.state} {unit}".strip()

        return self._get_text(lang, "not_found")

    def _get_text(self, lang: str, key: str) -> str:
        """Get localized phrase with fallback to English."""
        lang_dict = LOCALIZED_RESPONSES.get(lang, LOCALIZED_RESPONSES["en"])
        return lang_dict.get(key, LOCALIZED_RESPONSES["en"].get(key, "Done"))

    def _build_result(
        self,
        user_input: ConversationInput,
        speech_text: str,
        target_info: dict[str, Any] | None = None,
        debug_card: str | None = None,
    ) -> ConversationResult:
        """Construct a standardized Home Assistant ConversationResult."""
        intent_response = IntentResponse(language=user_input.language)
        if debug_card:
            speech_with_debug = f"{speech_text}\n\n[Laya Debug]\n{debug_card}"
            intent_response.async_set_speech(speech_with_debug)
        else:
            intent_response.async_set_speech(speech_text)

        if debug_card and hasattr(intent_response, "async_set_card"):
            try:
                intent_response.async_set_card(
                    title="Laya Debug",
                    content=debug_card,
                )
            except Exception:
                pass

        if target_info:
            target_id = target_info.get("id")
            if target_id:
                try:
                    from homeassistant.helpers import intent
                    t_type = (
                        intent.IntentResponseTargetType.AREA
                        if target_info.get("type") == "area"
                        else intent.IntentResponseTargetType.ENTITY
                    )
                    intent_response.async_set_results(
                        success_results=[intent.IntentResponseTarget(type=t_type, id=target_id)]
                    )
                except Exception:
                    pass
        return ConversationResult(
            response=intent_response,
            conversation_id=user_input.conversation_id,
        )
