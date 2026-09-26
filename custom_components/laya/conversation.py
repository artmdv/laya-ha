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
from homeassistant.helpers import area_registry, device_registry, entity_registry, intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.intent import IntentResponse, IntentResponseType

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
    CONF_TRANSLATE_TO_ENGLISH,
    CONF_TRANSLATION_URL,
    CONF_TRY_DEFAULT_AGENT_FIRST,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_EXPOSED_DOMAINS,
    DEFAULT_HIERARCHICAL_ROUTING,
    DEFAULT_NAME,
    DEFAULT_RESPONSE_STYLE,
    DEFAULT_TIMEOUT,
    DEFAULT_TRANSLATE_TO_ENGLISH,
    DEFAULT_TRANSLATION_URL,
    DEFAULT_TRY_DEFAULT_AGENT_FIRST,
    DOMAIN,
    LOCALIZED_RESPONSES,
    LOCALIZED_STATES,
    MAX_TARGET_CANDIDATES,
    STYLE_VERBOSE,
)
from .translator import async_translate_to_english

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


def _normalize_lithuanian_phonetics(text: str) -> str:
    """Normalize common Whisper phonetic slips and typos in Lithuanian smart home commands."""
    t = text.lower()
    # Voicing slips at word end for imperative verbs (e.g. atidaryg -> atidaryk, isjung -> isjunk)
    t = re.sub(r"\b(atidary|uždary|uzdary)g\b", r"\1k", t)
    t = re.sub(r"\b(i[šs]jun|[įi]jun)g\b", r"\1k", t)
    t = re.sub(r"\b(užgesin|uzgesin)g\b", r"\1k", t)
    return t


def _detect_deterministic_action(text: str) -> tuple[str | None, float]:
    """Detect unambiguous action intent from Lithuanian and English verb stems."""
    text_clean = _normalize_lithuanian_phonetics(text.lower())
    tokens = re.findall(r"\w+", text_clean)

    # 1. Questions / Inquiries
    if (
        text_clean.startswith("ar ")
        or text.strip().endswith("?")
        or any(text_clean.startswith(q) for q in ("kokia ", "koks ", "kiek ", "what ", "is ", "how "))
        or any(w in tokens for w in ("temperatūra", "temperatura", "būsena", "busena"))
    ):
        return "query_state", 1.0

    # 2. Lawnmower
    if any(
        w in tokens
        for w in (
            "žoliapjovė",
            "zoliapjove",
            "žoliapjovę",
            "žoliapjovės",
            "zoliapjoves",
            "žolę",
            "zole",
            "mower",
        )
    ):
        if any(w in tokens for w in ("stotel", "stotelę", "namo", "grįžk", "grizk", "baze", "dock")):
            return "dock_mower", 1.0
        if any(w in tokens for w in ("stop", "sustabdyk", "pristabdyk", "pause")):
            return "pause_mower", 1.0
        if any(w in tokens for w in ("pjauk", "paleisk", "start", "pradėk", "pradek", "pjauna")):
            return "start_mower", 1.0

    # 3. Vacuum
    if any(w in tokens for w in ("siurbk", "siurblys", "siurblį", "siurbli")):
        if any(w in tokens for w in ("stotel", "namo", "grįžk", "grizk", "baze")):
            return "dock_vacuum", 1.0
        if any(w in tokens for w in ("stop", "sustabdyk")):
            return "stop_vacuum", 1.0
        return "start_vacuum", 1.0

    # 4. Turn off (explicit off verbs)
    OFF_STEMS = (
        "išjunk", "isjunk", "išjungi", "isjungi", "išjungti", "isjungti",
        "išjunkite", "isjunkite", "užgesink", "uzgesink", "užgesinti",
        "uzgesinti", "užgesinkite", "uzgesinkite", "atjunk", "atjunkite",
        "turn off", "switch off", "power off"
    )
    for stem in OFF_STEMS:
        if stem in text_clean:
            return "turn_off", 1.0

    # 5. Turn on (explicit on verbs)
    ON_STEMS = (
        "įjunk", "ijunk", "įjungi", "ijungi", "įjungti", "ijungti",
        "įjunkite", "ijunkite", "uždek", "uzdek", "uždegti", "uzdegti",
        "uždekite", "uzdekite", "paleisk", "paleisti", "paleiskite",
        "turn on", "switch on", "power on"
    )
    for stem in ON_STEMS:
        if stem in text_clean:
            return "turn_on", 1.0

    # 6. Open cover / gate / blinds
    OPEN_STEMS = (
        "atidaryk", "atidarykite", "atidaryti", "atidarik",
        "atverk", "atverkite", "pakelk", "pakelkite", "open "
    )
    for stem in OPEN_STEMS:
        if stem in text_clean:
            return "open_cover", 1.0

    # 7. Close cover / gate / blinds
    CLOSE_STEMS = (
        "uždaryk", "uzdaryk", "uždarykite", "uzdarykite", "uždaryti", "uzdaryti",
        "užverk", "uzverk", "nuleisk", "nuleiskite", "close "
    )
    for stem in CLOSE_STEMS:
        if stem in text_clean:
            return "close_cover", 1.0

    # 8. Toggle
    TOGGLE_STEMS = ("perjunk", "perjunkite", "toggle")
    for stem in TOGGLE_STEMS:
        if stem in text_clean:
            return "toggle", 1.0

    return None, 0.0



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
            "lawn_mower",
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
        exposed_domains: list[str] = list(
            options.get(CONF_EXPOSED_DOMAINS, DEFAULT_EXPOSED_DOMAINS)
        )
        if "lawn_mower" not in exposed_domains:
            exposed_domains.append("lawn_mower")
        response_style: str = options.get(CONF_RESPONSE_STYLE, DEFAULT_RESPONSE_STYLE)
        hierarchical_routing: bool = options.get(
            CONF_HIERARCHICAL_ROUTING, DEFAULT_HIERARCHICAL_ROUTING
        )
        debug_logging: bool = options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
        # Step 0: Try Home Assistant built-in intent engine first if enabled
        try_default_agent: bool = options.get(
            CONF_TRY_DEFAULT_AGENT_FIRST, DEFAULT_TRY_DEFAULT_AGENT_FIRST
        )
        if try_default_agent:
            try:
                default_agent_id = getattr(conversation, "HOME_ASSISTANT_AGENT", "conversation.home_assistant")
                default_res = await conversation.async_converse(
                    hass=self.hass,
                    text=text,
                    conversation_id=user_input.conversation_id,
                    context=user_input.context,
                    agent_id=default_agent_id,
                    language=user_input.language,
                )
                if default_res and default_res.response:
                    resp_type = default_res.response.response_type
                    # If an intent was matched and executed or answered, return directly
                    if resp_type != IntentResponseType.ERROR:
                        if debug_logging:
                            _LOGGER.warning(
                                "Laya [DEBUG] Handled natively by Home Assistant built-in agent: %s",
                                getattr(default_res.response, "speech", ""),
                            )
                        return default_res
                    # If error was NOT no_intent_match (e.g. an actual service failure), return it too
                    err_code = (
                        default_res.response.data.get("code")
                        if default_res.response.data
                        else None
                    )
                    if err_code and err_code != "no_intent_match":
                        return default_res
                    if debug_logging:
                        _LOGGER.warning("Laya [DEBUG] Built-in agent returned no_intent_match, cascading to Laya")
            except Exception as err:
                _LOGGER.debug("Home Assistant default agent check bypassed: %s", err)

        # Step 0.5: Optional translation to English before querying Laya
        translate_to_en: bool = options.get(
            CONF_TRANSLATE_TO_ENGLISH, DEFAULT_TRANSLATE_TO_ENGLISH
        )
        translation_url: str = options.get(
            CONF_TRANSLATION_URL, DEFAULT_TRANSLATION_URL
        )

        query_text = text
        translated_for_debug = None
        if translate_to_en and lang != "en":
            session = async_get_clientsession(self.hass)
            translated = await async_translate_to_english(
                text=text,
                session=session,
                custom_url=translation_url,
                source_lang=lang,
            )
            if translated and translated.lower() != text.lower():
                query_text = translated
                translated_for_debug = translated
                if debug_logging:
                    _LOGGER.warning("Laya [DEBUG] Translated prompt '%s' -> '%s'", text, query_text)

        # 1. Discover registered areas and entities
        target_map, area_map = self._build_target_catalog(exposed_domains)

        if not target_map:
            _LOGGER.warning("Laya: No exposed entities found for domains: %s", exposed_domains)
            return self._build_result(user_input, self._get_text(lang, "not_found"))

        # Intelligent candidate filtering to stay safely under Laya head_max_len=256
        normalized_input = _normalize_lithuanian_phonetics(query_text)
        target_names = self._filter_target_candidates(normalized_input, target_map, MAX_TARGET_CANDIDATES)
        if len(target_map) > MAX_TARGET_CANDIDATES:
            _LOGGER.debug(
                "Filtered %d target candidates down to %d for '%s'",
                len(target_map),
                len(target_names),
                normalized_input,
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
                        text=normalized_input,
                        action_criteria=action_criteria,
                        target_map=target_map,
                        area_map=area_map,
                        exposed_domains=exposed_domains,
                        confidence_threshold=confidence_threshold,
                    )
                )
            else:
                decision = await self.client.decide(
                    command=normalized_input,
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
        # Deterministic action disambiguation: fixes Lithuanian verb antonym confusion
        # (išjunk vs įjunk, open vs close) and Whisper phonetic slips (atidaryg, isjung)
        det_action, det_conf = _detect_deterministic_action(text)
        if not det_action and query_text != text:
            det_action, det_conf = _detect_deterministic_action(query_text)

        if det_action:
            if action_choice.choice != det_action:
                _LOGGER.info(
                    "Deterministic override: action '%s' (conf=%.2f) -> '%s' (conf=%.2f) for '%s'",
                    action_choice.choice,
                    action_choice.confidence,
                    det_action,
                    det_conf,
                    text,
                )
            action_choice = DecisionChoice(
                choice=det_action,
                confidence=max(action_choice.confidence, det_conf),
                probabilities=action_choice.probabilities,
            )

        debug_lines = []
        if translated_for_debug:
            debug_lines.append(f"Translated: {translated_for_debug}")
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
                speech = self._query_area_state(area_id, target_name, lang, text)
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
                elif any(w in text_lower for w in ("mower", "žoliapjov", "zoliapjov", "žol", "zol")):
                    target_domain = "lawn_mower"
                elif any(w in text_lower for w in ("vacuum", "siurbl")):
                    target_domain = "vacuum"
                elif any(w in text_lower for w in ("media", "muzik", "grotuv", "televizor", "tv", "player")):
                    target_domain = "media_player"

                if target_domain == "lawn_mower":
                    if service_name in ("turn_on", "start"):
                        target_service = "lawn_mower.start_mowing"
                    elif service_name in ("turn_off", "return_to_base", "dock"):
                        target_service = "lawn_mower.dock"
                    elif service_name in ("pause", "stop"):
                        target_service = "lawn_mower.pause"
                    else:
                        target_service = service_full
                elif service_domain == "homeassistant":
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
                ent_domain = resolved_target.get("domain") or entity_id.split(".")[0]
                if ent_domain == "lawn_mower":
                    if service_domain == "homeassistant":
                        if service_name == "turn_on":
                            service_full = "lawn_mower.start_mowing"
                        elif service_name == "turn_off":
                            service_full = "lawn_mower.dock"
                    elif service_domain == "vacuum":
                        if service_name in ("start", "start_cleaning"):
                            service_full = "lawn_mower.start_mowing"
                        elif service_name in ("pause", "stop"):
                            service_full = "lawn_mower.pause"
                        elif service_name in ("return_to_base", "dock"):
                            service_full = "lawn_mower.dock"
                    service_domain, service_name = service_full.split(".", 1)

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
        device_area_entities: dict[str, list[tuple[str, str]]] = {}

        for state in self.hass.states.async_all():
            if state.domain not in exposed_domains:
                continue

            ent_entry = ent_reg.async_get(state.entity_id) if ent_reg else None
            dev_id = getattr(ent_entry, "device_id", None) if ent_entry else None
            ent_area_id = None
            if ent_entry:
                ent_area_id = getattr(ent_entry, "area_id", None) or (
                    dev_area_map.get(dev_id) if dev_id else None
                )

            if ent_area_id == area_id:
                friendly_name = state.attributes.get("friendly_name")
                name_to_use = _safe_str(friendly_name) or state.entity_id
                area_entities[name_to_use] = {
                    "type": "entity",
                    "id": state.entity_id,
                    "domain": state.domain,
                }
                if dev_id:
                    device_area_entities.setdefault(dev_id, []).append((state.entity_id, state.domain))

                if ent_entry and hasattr(ent_entry, "aliases") and ent_entry.aliases:
                    for alias in ent_entry.aliases:
                        alias_str = _safe_str(alias)
                        if alias_str:
                            area_entities[alias_str] = {
                                "type": "entity",
                                "id": state.entity_id,
                                "domain": state.domain,
                            }

        # Associate device-level names and aliases in this area
        PRIMARY_DOMAINS = (
            "lawn_mower",
            "vacuum",
            "light",
            "cover",
            "climate",
            "media_player",
            "fan",
            "lock",
            "switch",
            "binary_sensor",
            "sensor",
        )
        if dev_reg:
            for dev_id, ent_list in device_area_entities.items():
                dev = dev_reg.async_get(dev_id) if hasattr(dev_reg, "async_get") else getattr(dev_reg, "devices", {}).get(dev_id)
                if not dev:
                    continue

                def _domain_rank(item: tuple[str, str]) -> int:
                    try:
                        return PRIMARY_DOMAINS.index(item[1])
                    except ValueError:
                        return 999

                sorted_entities = sorted(ent_list, key=_domain_rank)
                primary_entity_id, primary_domain = sorted_entities[0]

                dev_name_user = getattr(dev, "name_by_user", None)
                if dev_name_user:
                    dev_name_str = _safe_str(dev_name_user)
                    if dev_name_str and dev_name_str not in area_entities:
                        area_entities[dev_name_str] = {"type": "entity", "id": primary_entity_id, "domain": primary_domain}

                dev_aliases = getattr(dev, "aliases", None)
                if dev_aliases:
                    for alias in dev_aliases:
                        alias_str = _safe_str(alias)
                        if alias_str:
                            area_entities[alias_str] = {"type": "entity", "id": primary_entity_id, "domain": primary_domain}

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
        """Collect all matching device friendly names, device aliases, and registered areas."""
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
        dev_reg = device_registry.async_get(self.hass)
        device_exposed_entities: dict[str, list[tuple[str, str]]] = {}

        for state in self.hass.states.async_all():
            domain = state.domain
            if domain not in exposed_domains:
                continue

            entity_id = state.entity_id
            friendly_name = state.attributes.get("friendly_name")
            name_to_use = _safe_str(friendly_name) or entity_id

            target_map[name_to_use] = {"type": "entity", "id": entity_id, "domain": domain}

            # Check entity registry for extra aliases and device mapping
            ent_entry = ent_reg.async_get(entity_id) if ent_reg else None
            if ent_entry:
                if getattr(ent_entry, "device_id", None):
                    device_exposed_entities.setdefault(ent_entry.device_id, []).append((entity_id, domain))

                if hasattr(ent_entry, "aliases") and ent_entry.aliases:
                    for alias in ent_entry.aliases:
                        alias_str = _safe_str(alias)
                        if alias_str:
                            target_map[alias_str] = {"type": "entity", "id": entity_id, "domain": domain}

        # 3. Associate Device-level Aliases and User Names with Primary Entity
        PRIMARY_DOMAINS = (
            "lawn_mower",
            "vacuum",
            "light",
            "cover",
            "climate",
            "media_player",
            "fan",
            "lock",
            "switch",
            "binary_sensor",
            "sensor",
        )
        if dev_reg:
            for dev_id, ent_list in device_exposed_entities.items():
                dev = dev_reg.async_get(dev_id) if hasattr(dev_reg, "async_get") else getattr(dev_reg, "devices", {}).get(dev_id)
                if not dev:
                    continue

                def _domain_rank(item: tuple[str, str]) -> int:
                    try:
                        return PRIMARY_DOMAINS.index(item[1])
                    except ValueError:
                        return 999

                sorted_entities = sorted(ent_list, key=_domain_rank)
                primary_entity_id, primary_domain = sorted_entities[0]

                # User-assigned device name
                dev_name_user = getattr(dev, "name_by_user", None)
                if dev_name_user:
                    dev_name_str = _safe_str(dev_name_user)
                    if dev_name_str and dev_name_str not in target_map:
                        target_map[dev_name_str] = {"type": "entity", "id": primary_entity_id, "domain": primary_domain}

                # Device-level aliases (e.g. Žolės pjovimo robotas, Žolės robotas, Žoliapjovė)
                dev_aliases = getattr(dev, "aliases", None)
                if dev_aliases:
                    for alias in dev_aliases:
                        alias_str = _safe_str(alias)
                        if alias_str:
                            target_map[alias_str] = {"type": "entity", "id": primary_entity_id, "domain": primary_domain}

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
        domain = getattr(state_obj, "domain", None) or state_obj.entity_id.split(".")[0]
        device_class = state_obj.attributes.get("device_class")

        state_dict = LOCALIZED_STATES.get(lang, LOCALIZED_STATES["en"])
        is_word = state_dict.get("is", "is")

        # Handle numeric / measurement states (e.g. 21.5 °C, 55%, 150 W)
        if unit:
            if lang == "lt":
                if "temperat" in target_name.lower() or device_class == "temperature":
                    return f"{target_name} yra {raw_state} {unit}"
            return f"{target_name} {is_word} {raw_state} {unit}"

        # Handle covers and door/window opening sensors
        if domain == "cover" or device_class in ("door", "garage_door", "window", "opening", "gate"):
            if lang == "lt":
                # Check for plural nouns in Lithuanian (e.g. vartai, durys, žaliuzės)
                is_plural = any(target_name.lower().endswith(end) for end in ("ai", "ys", "ės", "es"))
                if raw_state in ("open", "on"):
                    val = "atidaryti" if is_plural else "atidaryta"
                elif raw_state in ("closed", "off"):
                    val = "uždaryti" if is_plural else "uždaryta"
                elif raw_state == "opening":
                    val = "atsidaro"
                elif raw_state == "closing":
                    val = "užsidaro"
                else:
                    val = state_dict.get(raw_state.lower(), raw_state)
                return f"{target_name} {is_word} {val}"
            else:
                val = "open" if raw_state in ("open", "on") else "closed" if raw_state in ("closed", "off") else raw_state
                return f"{target_name} is {val}"

        if domain == "lock" or device_class == "lock":
            if lang == "lt":
                val = "užrakinta" if raw_state in ("locked", "off") else "atrakinta"
                return f"{target_name} {is_word} {val}"
            else:
                val = "locked" if raw_state in ("locked", "off") else "unlocked"
                return f"{target_name} is {val}"

        # Handle lawn mowers
        if domain == "lawn_mower" or any(w in target_name.lower() for w in ("žoliapjov", "zoliapjov", "mower")):
            if lang == "lt":
                if raw_state == "mowing":
                    return f"{target_name} pjauna žolę" if "žol" not in target_name.lower() else f"{target_name} pjauna"
                elif raw_state == "docked":
                    return f"{target_name} yra stotelėje"
                elif raw_state in ("paused", "stop", "stopped"):
                    return f"{target_name} yra pristabdyta"
                elif raw_state in ("error", "problem"):
                    return f"{target_name}: klaida"
                elif raw_state in ("returning", "returning_to_dock"):
                    return f"{target_name} grįžta į stotelę"
                else:
                    val = state_dict.get(raw_state.lower(), raw_state)
                    return f"{target_name} {is_word} {val}"
            else:
                if raw_state == "mowing":
                    return f"{target_name} is mowing"
                elif raw_state == "docked":
                    return f"{target_name} is docked"
                elif raw_state in ("paused", "stop", "stopped"):
                    return f"{target_name} is paused"
                elif raw_state in ("error", "problem"):
                    return f"{target_name} reported an error"
                elif raw_state in ("returning", "returning_to_dock"):
                    return f"{target_name} is returning to dock"
                else:
                    return f"{target_name} is {raw_state}"

        # Handle binary motion / occupancy sensors
        if domain == "binary_sensor" and device_class in ("motion", "occupancy"):
            if lang == "lt":
                return f"{target_name}: užfiksuotas judesys" if raw_state == "on" else f"{target_name}: judesio nėra"
            return f"{target_name}: motion detected" if raw_state == "on" else f"{target_name}: clear"

        # Discrete states (on/off, etc.)
        translated_state = state_dict.get(raw_state.lower(), raw_state)
        return f"{target_name} {is_word} {translated_state}"

    def _query_area_state(self, area_id: str, area_name: str, lang: str, text: str = "") -> str:
        """Find and format the most relevant state for an area query (temperature, lights, covers)."""
        state_dict = LOCALIZED_STATES.get(lang, LOCALIZED_STATES["en"])
        is_word = state_dict.get("is", "is")
        text_lower = text.lower()

        is_light_query = any(w in text_lower for w in ("švies", "svies", "lemp", "apšviet", "apsviet", "light"))
        is_humidity_query = any(w in text_lower for w in ("drėgm", "dregm", "humidity"))
        is_cover_query = any(w in text_lower for w in ("vart", "užuolaid", "uzuolaid", "rolet", "žaliuz", "zaliuz", "cover", "blind", "gate"))
        is_door_query = any(w in text_lower for w in ("dur", "lang", "door", "window"))
        is_mower_query = any(w in text_lower for w in ("žoliapjov", "zoliapjov", "žol", "zol", "mow"))

        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)

        dev_area_map: dict[str, str] = {}
        if dev_reg and hasattr(dev_reg, "devices"):
            for dev in dev_reg.devices.values():
                if getattr(dev, "area_id", None):
                    dev_area_map[dev.id] = dev.area_id

        area_lower = area_name.lower()
        area_stem = area_lower[:4] if len(area_lower) >= 4 else area_lower

        area_lights = []
        area_covers = []
        area_doors = []
        area_mowers = []
        area_temp = None
        area_humidity = None

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

            # Check if entity belongs to this area
            matches_area = (
                ent_area_id == area_id
                or area_lower in state.entity_id.lower()
                or area_id in state.entity_id.lower()
                or (ent_entry and hasattr(ent_entry, "name") and ent_entry.name and area_stem in ent_entry.name.lower())
            )
            if not matches_area:
                continue

            domain = state.domain
            dev_class = state.attributes.get("device_class")

            if domain == "light":
                area_lights.append(state)
            elif domain == "cover":
                area_covers.append(state)
            elif domain == "lawn_mower":
                area_mowers.append(state)
            elif domain == "binary_sensor" and dev_class in ("door", "garage_door", "window", "opening"):
                area_doors.append(state)
            elif domain == "climate" and "current_temperature" in state.attributes and not area_temp:
                area_temp = (state.attributes["current_temperature"], getattr(getattr(self.hass.config, "units", None), "temperature_unit", "°C"))
            elif domain == "sensor" and (
                dev_class == "temperature"
                or "temperature" in state.entity_id.lower()
                or "temperat" in state.entity_id.lower()
                or state.attributes.get("unit_of_measurement") in ("°C", "°F", "K")
            ) and not area_temp:
                area_temp = (state.state, state.attributes.get("unit_of_measurement", "°C"))
            elif domain == "sensor" and (dev_class == "humidity" or "drėgm" in state.entity_id.lower() or "dregm" in state.entity_id.lower() or "%" in str(state.attributes.get("unit_of_measurement", ""))) and not area_humidity:
                area_humidity = (state.state, state.attributes.get("unit_of_measurement", "%"))

        # 1. Light Query
        if is_light_query and area_lights:
            on_lights = [l for l in area_lights if l.state == "on"]
            if lang == "lt":
                return f"{area_name} šviesa yra įjungta" if on_lights else f"{area_name} visos šviesos yra išjungtos"
            return f"Lights in {area_name} are on" if on_lights else f"Lights in {area_name} are off"

        # 2. Cover / Gate Query
        if is_cover_query and area_covers:
            open_covers = [c for c in area_covers if c.state in ("open", "opening")]
            if lang == "lt":
                return f"{area_name} yra atidaryta" if open_covers else f"{area_name} yra uždaryta"
            return f"{area_name} is open" if open_covers else f"{area_name} is closed"

        # 3. Door / Window Query
        if is_door_query and area_doors:
            open_doors = [d for d in area_doors if d.state == "on"]
            if lang == "lt":
                return f"{area_name} yra atidaryta" if open_doors else f"{area_name} viskas uždaryta"
            return f"{area_name} is open" if open_doors else f"{area_name} is closed"

        # 4. Lawnmower Query
        if is_mower_query and area_mowers:
            mower = area_mowers[0]
            mower_name = _safe_str(mower.attributes.get("friendly_name")) or area_name
            return self._format_state_query_response(mower_name, mower, lang)

        # 5. Humidity Query
        if is_humidity_query and area_humidity:
            val, unit = area_humidity
            if lang == "lt":
                return f"{area_name} drėgmė yra {val} {unit}"
            return f"{area_name} humidity is {val} {unit}"

        # 5. Temperature Query (or default state inquiry for area)
        if area_temp:
            val, unit = area_temp
            if lang == "lt":
                return f"{area_name} temperatūra yra {val} {unit}"
            return f"{area_name} temperature is {val} {unit}"

        # Fallback to lights status if any lights exist
        if area_lights:
            on_lights = [l for l in area_lights if l.state == "on"]
            if lang == "lt":
                return f"{area_name} šviesa yra įjungta" if on_lights else f"{area_name} visos šviesos yra išjungtos"
            return f"Lights in {area_name} are on" if on_lights else f"Lights in {area_name} are off"

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
