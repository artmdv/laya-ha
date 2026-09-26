"""Unit tests for decision processing, localization, and response formatting."""

import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

if "homeassistant" not in sys.modules:
    ha_mock = MagicMock()

    class _MockConversationEntity:
        pass

    class _MockIntentResponseType:
        ACTION_DONE = "action_done"
        ERROR = "error"
        QUERY_ANSWER = "query_answer"

    class _MockIntentResponse:
        def __init__(self, language="en"):
            self.language = language
            self.speech = None
            self.speech_text = None

        def async_set_speech(self, text):
            self.speech_text = text
            self.speech = {"plain": {"speech": text}}

        def async_set_card(self, title, content):
            pass

        def async_set_results(self, success_results=None, failed_results=None):
            pass

    class _MockConversationResult:
        def __init__(self, response, conversation_id=None):
            self.response = response
            self.conversation_id = conversation_id

    ha_mock.ConversationEntity = _MockConversationEntity
    ha_mock.components.conversation.ConversationEntity = _MockConversationEntity
    ha_mock.helpers.intent.IntentResponseType = _MockIntentResponseType
    ha_mock.helpers.intent.IntentResponse = _MockIntentResponse
    ha_mock.components.conversation.ConversationResult = _MockConversationResult

    _default_conv_res = MagicMock()
    _default_conv_res.response.response_type = "error"
    _default_conv_res.response.data = {"code": "no_intent_match"}
    ha_mock.components.conversation.async_converse = AsyncMock(return_value=_default_conv_res)

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

from homeassistant.helpers.intent import IntentResponseType
from custom_components.laya.client import DecisionChoice
from custom_components.laya.const import (
    ACTION_DEFINITIONS,
    DEFAULT_EXPOSED_DOMAINS,
    LOCALIZED_RESPONSES,
    MAX_TARGET_CANDIDATES,
    STYLE_CONCISE,
    STYLE_VERBOSE,
)
from custom_components.laya.conversation import LayaConversationEntity


class TestDecisionLogic(unittest.IsolatedAsyncioTestCase):
    """Test suite for action mappings, localization, and target resolution."""

    def test_all_actions_have_descriptions_and_services(self):
        """Verify all defined actions have valid service and description strings."""
        for action_name, action_data in ACTION_DEFINITIONS.items():
            self.assertIn("description", action_data, f"Missing description for {action_name}")
            self.assertIn("service", action_data, f"Missing service for {action_name}")
            if action_data["service"] is not None:
                self.assertIn(".", action_data["service"], f"Invalid service format for {action_name}")
            self.assertTrue(len(action_data["description"]) > 5)

    def test_localization_completeness(self):
        """Ensure English and Lithuanian translation dictionaries cover core action keys."""
        core_keys = [
            "turn_on",
            "turn_off",
            "toggle",
            "open_cover",
            "close_cover",
            "start_vacuum",
            "stop_vacuum",
            "dock_vacuum",
            "not_found",
            "low_confidence",
            "error",
            "done",
        ]
        for lang in ("en", "lt"):
            self.assertIn(lang, LOCALIZED_RESPONSES)
            lang_dict = LOCALIZED_RESPONSES[lang]
            for key in core_keys:
                self.assertIn(key, lang_dict, f"Language '{lang}' missing key '{key}'")
                self.assertTrue(len(lang_dict[key]) > 0)

    def test_default_exposed_domains(self):
        """Verify default exposed domains contain common controllable home devices."""
        self.assertIn("light", DEFAULT_EXPOSED_DOMAINS)
        self.assertIn("switch", DEFAULT_EXPOSED_DOMAINS)
        self.assertIn("vacuum", DEFAULT_EXPOSED_DOMAINS)
        self.assertIn("cover", DEFAULT_EXPOSED_DOMAINS)

    def test_verbose_formatting_english(self):
        """Test verbose response formatting in English."""
        action = "turn_off"
        target = "Living Room Light"
        base_text = LOCALIZED_RESPONSES["en"][action]
        verbose = f"{base_text} {target}"
        self.assertEqual(verbose, "Turned off Living Room Light")

    def test_verbose_formatting_lithuanian(self):
        """Test verbose response formatting in Lithuanian."""
        action = "turn_off"
        target = "Svetainės šviesa"
        base_text = LOCALIZED_RESPONSES["lt"][action]
        verbose = f"{base_text} ({target})"
        self.assertEqual(verbose, "Išjungta (Svetainės šviesa)")

    def test_state_query_formatting_english(self):
        """Test formatting of state queries in English."""
        # Numeric state with unit
        target = "Outside Temperature"
        state = "18.5"
        unit = "°C"
        formatted = f"{target} is {state} {unit}"
        self.assertEqual(formatted, "Outside Temperature is 18.5 °C")

        # Discrete state
        gate = "Front Gate"
        status = "closed"
        formatted_gate = f"{gate} is {status}"
        self.assertEqual(formatted_gate, "Front Gate is closed")

    def test_state_query_formatting_lithuanian(self):
        """Test formatting of state queries in Lithuanian."""
        # Numeric state with unit
        target = "Lauko temperatūra"
        state = "18.5"
        unit = "°C"
        formatted = f"{target} yra {state} {unit}"
        self.assertEqual(formatted, "Lauko temperatūra yra 18.5 °C")

        # Discrete state (e.g. vartai uždaryti)
        gate = "Kiemo vartai"
        status = "uždaryta"
        formatted_gate = f"{gate} yra {status}"
        self.assertEqual(formatted_gate, "Kiemo vartai yra uždaryta")

    def test_filter_candidates_below_limit(self):
        """When total candidates are within limit, all candidates should be returned."""
        catalog = {
            f"device_{i}": {"type": "entity", "id": f"light.dev_{i}", "domain": "light"}
            for i in range(10)
        }
        res = LayaConversationEntity._filter_target_candidates("turn on light", catalog, max_limit=160)
        self.assertEqual(len(res), 10)
        self.assertEqual(set(res), set(catalog.keys()))

    def test_filter_candidates_exceeding_limit_prioritizes_match_and_devices(self):
        """When candidates exceed head_max_len limit, relevant targets must be prioritized."""
        catalog = {}
        # 5 areas
        catalog["Virtuvė"] = {"type": "area", "id": "kitchen", "domain": "area"}
        catalog["Svetainė"] = {"type": "area", "id": "living_room", "domain": "area"}
        catalog["Miegamasis"] = {"type": "area", "id": "bedroom", "domain": "area"}
        catalog["Vonios kambarys"] = {"type": "area", "id": "bathroom", "domain": "area"}
        catalog["Kiemas"] = {"type": "area", "id": "yard", "domain": "area"}

        # Controllable matching devices
        catalog["Virtuvės šviestuvas"] = {"type": "entity", "id": "light.kitchen_main", "domain": "light"}
        catalog["Svetainės šviesa"] = {"type": "entity", "id": "light.living_main", "domain": "light"}

        # 300 background sensor entities
        for i in range(300):
            catalog[f"Serverio CPU apkrova {i}"] = {
                "type": "entity",
                "id": f"sensor.cpu_{i}",
                "domain": "sensor",
            }

        filtered = LayaConversationEntity._filter_target_candidates(
            text="įjunk šviesą virtuvėj",
            target_map=catalog,
            max_limit=50,
        )

        self.assertEqual(len(filtered), 50)
        # Check that top candidates contain the exact matches
        self.assertIn("Virtuvės šviestuvas", filtered[:5])
        self.assertIn("Virtuvė", filtered[:5])
        self.assertIn("Svetainės šviesa", filtered)

    def test_filter_candidates_state_query_prioritizes_sensor(self):
        """Sensors matching state query terms should be prioritized in candidates."""
        catalog = {}
        # 250 irrelevant switches
        for i in range(250):
            catalog[f"Relė {i}"] = {
                "type": "entity",
                "id": f"switch.relay_{i}",
                "domain": "switch",
            }

        catalog["Lauko temperatūra"] = {
            "type": "entity",
            "id": "sensor.outdoor_temp",
            "domain": "sensor",
        }
        catalog["Kiemo vartai"] = {
            "type": "entity",
            "id": "binary_sensor.gate_state",
            "domain": "binary_sensor",
        }

        filtered = LayaConversationEntity._filter_target_candidates(
            text="kokia lauko temperatūra?",
            target_map=catalog,
            max_limit=10,
        )

        self.assertEqual(len(filtered), 10)
        self.assertEqual(filtered[0], "Lauko temperatūra")

    async def test_hierarchical_routing_identifies_room_and_device(self):
        """Test two-step hierarchical routing resolves area first, then device within that area."""
        mock_client = MagicMock()
        # Step 1 response: action=turn_on, area=Virtuvė
        step1_result = {
            "action": DecisionChoice(choice="turn_on", confidence=0.95, probabilities={}),
            "area": DecisionChoice(choice="Virtuvė", confidence=0.98, probabilities={}),
        }
        # Step 2 response: target=Virtuvės šviestuvas
        step2_result = {
            "target": DecisionChoice(choice="Virtuvės šviestuvas", confidence=0.96, probabilities={}),
        }

        mock_client.query = AsyncMock(side_effect=[step1_result, step2_result])

        entity = LayaConversationEntity(
            hass=MagicMock(),
            entry=MagicMock(),
            client=mock_client,
        )

        # Mock _get_entities_in_area to return kitchen devices
        entity._get_entities_in_area = MagicMock(
            return_value={
                "Virtuvės šviestuvas": {"type": "entity", "id": "light.kitchen_lamp", "domain": "light"},
                "Virtuvės stalviršio LED": {"type": "entity", "id": "light.kitchen_led", "domain": "light"},
            }
        )

        area_map = {"kitchen_id": "Virtuvė", "living_id": "Svetainė"}
        target_map = {
            "Virtuvės šviestuvas": {"type": "entity", "id": "light.kitchen_lamp", "domain": "light"},
            "Virtuvės stalviršio LED": {"type": "entity", "id": "light.kitchen_led", "domain": "light"},
            "Svetainės šviesa": {"type": "entity", "id": "light.living_main", "domain": "light"},
        }

        action_choice, target_choice, result_targets, resolved_area = (
            await entity._async_process_hierarchical(
                text="įjunk šviesą virtuvėj",
                action_criteria={"turn_on": "Turn on a light"},
                target_map=target_map,
                area_map=area_map,
                exposed_domains=["light"],
                confidence_threshold=0.50,
            )
        )

        self.assertEqual(action_choice.choice, "turn_on")
        self.assertEqual(target_choice.choice, "Virtuvės šviestuvas")
        self.assertEqual(resolved_area, "Virtuvė")
        self.assertIn("Virtuvės šviestuvas", result_targets)
        self.assertEqual(mock_client.query.call_count, 2)

    async def test_hierarchical_routing_fallback_when_no_area(self):
        """When no area is detected, hierarchical routing falls back to global targets seamlessly."""
        mock_client = MagicMock()
        # Step 1: action=start_vacuum, area=none
        step1_result = {
            "action": DecisionChoice(choice="start_vacuum", confidence=0.92, probabilities={}),
            "area": DecisionChoice(choice="none", confidence=0.90, probabilities={}),
        }
        # Fallback Step 2: target=Siurblys
        step2_result = {
            "target": DecisionChoice(choice="Siurblys", confidence=0.94, probabilities={}),
        }

        mock_client.query = AsyncMock(side_effect=[step1_result, step2_result])

        entity = LayaConversationEntity(
            hass=MagicMock(),
            entry=MagicMock(),
            client=mock_client,
        )

        area_map = {"kitchen_id": "Virtuvė"}
        target_map = {
            "Siurblys": {"type": "entity", "id": "vacuum.roborock", "domain": "vacuum"},
            "Virtuvės šviestuvas": {"type": "entity", "id": "light.kitchen_lamp", "domain": "light"},
        }

        action_choice, target_choice, result_targets, resolved_area = (
            await entity._async_process_hierarchical(
                text="pradėk siurbti",
                action_criteria={"start_vacuum": "Start vacuum cleaner"},
                target_map=target_map,
                area_map=area_map,
                exposed_domains=["vacuum"],
                confidence_threshold=0.50,
            )
        )

        self.assertEqual(action_choice.choice, "start_vacuum")
        self.assertEqual(target_choice.choice, "Siurblys")
        self.assertIsNone(resolved_area)
        self.assertEqual(mock_client.query.call_count, 2)

    async def test_hierarchical_routing_direct_area_match(self):
        """Verify direct room mention (e.g. virtuvėj) selects the area and filters candidates."""
        mock_client = MagicMock()
        mock_client.query = AsyncMock(
            side_effect=[
                {
                    "action": DecisionChoice(choice="turn_on", confidence=0.85, probabilities={}),
                    "area": DecisionChoice(choice="none", confidence=0.10, probabilities={}),
                },
                {
                    "target": DecisionChoice(choice="Virtuvės šviestuvas", confidence=0.90, probabilities={}),
                },
            ]
        )

        mock_hass = MagicMock()
        mock_area = MagicMock()
        mock_area.name = "Virtuvė"
        mock_area.id = "kitchen_id"
        mock_hass.data = {}

        mock_area_reg = MagicMock()
        mock_area_reg.areas.values.return_value = [mock_area]

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )

        area_map = {"kitchen_id": "Virtuvė"}
        target_map = {
            "Laukas": {"type": "area", "id": "laukas", "domain": "homeassistant"},
            "Virtuvės šviestuvas": {"type": "entity", "id": "light.kitchen_lamp", "domain": "light", "area_id": "kitchen_id"},
        }

        with unittest.mock.patch.object(
            entity,
            "_get_entities_in_area",
            return_value={
                "Virtuvės šviestuvas": {
                    "type": "entity",
                    "id": "light.kitchen_lamp",
                    "domain": "light",
                },
                "Virtuvės LED": {
                    "type": "entity",
                    "id": "light.kitchen_led",
                    "domain": "light",
                },
            },
        ):
            action_choice, target_choice, result_targets, resolved_area = (
                await entity._async_process_hierarchical(
                    text="įjunk šviesą virtuvėj",
                    action_criteria={"turn_on": "Turn on"},
                    target_map=target_map,
                    area_map=area_map,
                    exposed_domains=["light"],
                    confidence_threshold=0.50,
                )
            )

        self.assertEqual(resolved_area, "Virtuvė")
        self.assertEqual(target_choice.choice, "Virtuvės šviestuvas")
        self.assertIn("Virtuvės šviestuvas", result_targets)
        self.assertNotIn("Laukas", result_targets)

    async def test_interrogative_question_override_to_query_state(self):
        """Verify questions starting with 'ar' override any action command to query_state."""
        mock_client = MagicMock()
        decision_mock = MagicMock()
        decision_mock.action = DecisionChoice(choice="turn_on", confidence=0.65, probabilities={})
        decision_mock.target = DecisionChoice(choice="Virtuvės šviesa", confidence=0.88, probabilities={})
        mock_client.decide = AsyncMock(return_value=decision_mock)

        mock_hass = MagicMock()
        light_state = MagicMock()
        light_state.state = "on"
        light_state.attributes = {"friendly_name": "Virtuvės šviesa"}
        mock_hass.states.get.return_value = light_state

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )

        user_input = MagicMock()
        user_input.text = "ar virtuvėj įjungta šviesa?"
        user_input.language = "lt"
        user_input.conversation_id = "test_conv"

        with unittest.mock.patch.object(entity, "_build_target_catalog") as mock_cat:
            mock_cat.return_value = (
                {"Virtuvės šviesa": {"type": "entity", "id": "light.kitchen", "domain": "light"}},
                {"kitchen_id": "Virtuvė"},
            )
            entity.entry.options = {"hierarchical_routing": False}
            res = await entity._async_process_internal(user_input)

            # It must NOT execute turn_on, but rather query the state
            mock_hass.services.async_call.assert_not_called()

    async def test_area_service_call_targets_light_domain_never_homeassistant(self):
        """Verify area turn_on executes light.turn_on, preventing turning on appliances like dishwashers."""
        mock_client = MagicMock()
        decision_mock = MagicMock()
        decision_mock.action = DecisionChoice(choice="turn_on", confidence=0.95, probabilities={})
        decision_mock.target = DecisionChoice(choice="Kitchen", confidence=0.98, probabilities={})
        mock_client.decide = AsyncMock(return_value=decision_mock)

        mock_hass = MagicMock()
        mock_hass.services.has_service.return_value = True
        mock_hass.services.async_call = AsyncMock()

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )

        user_input = MagicMock()
        user_input.text = "turn on kitchen light"
        user_input.language = "en"
        user_input.conversation_id = "test_conv"

        with unittest.mock.patch.object(entity, "_build_target_catalog") as mock_cat:
            mock_cat.return_value = (
                {"Kitchen": {"type": "area", "id": "kitchen", "domain": "area"}},
                {"kitchen": "Kitchen"},
            )
            entity.entry.options = {"hierarchical_routing": False}
            res = await entity._async_process_internal(user_input)

            # Crucial assertion: Must call light.turn_on, NEVER homeassistant.turn_on!
            mock_hass.services.async_call.assert_called_once_with(
                "light",
                "turn_on",
                service_data={},
                target={"area_id": "kitchen"},
                blocking=True,
            )

    async def test_area_synonym_matching_english_area_lithuanian_utterance(self):
        """Verify English area 'Kitchen' is matched when user speaks Lithuanian 'virtuvėj'."""
        mock_client = MagicMock()
        mock_client.query = AsyncMock(
            side_effect=[
                {
                    "action": DecisionChoice(choice="turn_on", confidence=0.95, probabilities={}),
                    "area": DecisionChoice(choice="none", confidence=0.10, probabilities={}),
                },
                {
                    "target": DecisionChoice(choice="Virtuvės šviesa", confidence=0.92, probabilities={}),
                },
            ]
        )

        mock_hass = MagicMock()
        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )

        area_map = {"kitchen": "Kitchen"}
        target_map = {
            "Kitchen": {"type": "area", "id": "kitchen", "domain": "area"},
            "Virtuvės šviesa": {"type": "entity", "id": "light.kitchen", "domain": "light", "area_id": "kitchen"},
        }

        with unittest.mock.patch.object(
            entity,
            "_get_entities_in_area",
            return_value={
                "Virtuvės šviesa": {"type": "entity", "id": "light.kitchen", "domain": "light"},
            },
        ):
            action_choice, target_choice, result_targets, resolved_area = (
                await entity._async_process_hierarchical(
                    text="įjunk šviesą virtuvėj",
                    action_criteria={"turn_on": "Turn on"},
                    target_map=target_map,
                    area_map=area_map,
                    exposed_domains=["light"],
                    confidence_threshold=0.50,
                )
            )

        self.assertEqual(resolved_area, "Kitchen")
        self.assertEqual(target_choice.choice, "Virtuvės šviesa")
        self.assertIn("Virtuvės šviesa", result_targets)

    async def test_default_agent_success_bypasses_laya(self):
        """When built-in HA agent handles intent, it returns immediately without calling Laya."""
        mock_client = MagicMock()
        mock_hass = MagicMock()
        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )
        entity.entry.options = {"try_default_agent_first": True}

        user_input = MagicMock()
        user_input.text = "įjunk šviesą"
        user_input.language = "lt"
        user_input.conversation_id = "test_conv"
        user_input.context = MagicMock()

        success_res = MagicMock()
        success_res.response.response_type = IntentResponseType.ACTION_DONE
        success_res.response.speech = {"plain": {"speech": "Įjungta"}}

        with unittest.mock.patch("custom_components.laya.conversation.conversation.async_converse", new_callable=AsyncMock) as mock_conv:
            mock_conv.return_value = success_res
            res = await entity._async_process_internal(user_input)
            self.assertEqual(res, success_res)
            mock_client.decide.assert_not_called()
            mock_client.query.assert_not_called()

    async def test_default_agent_no_intent_cascades_to_laya(self):
        """When built-in HA agent returns no_intent_match, Laya processes the command."""
        mock_client = MagicMock()
        decision_mock = MagicMock()
        decision_mock.action = DecisionChoice(choice="turn_on", confidence=0.95, probabilities={})
        decision_mock.target = DecisionChoice(choice="Virtuvės šviesa", confidence=0.98, probabilities={})
        mock_client.decide = AsyncMock(return_value=decision_mock)

        mock_hass = MagicMock()
        mock_hass.services.has_service.return_value = True
        mock_hass.services.async_call = AsyncMock()

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )
        entity.entry.options = {
            "try_default_agent_first": True,
            "translate_to_english": False,
            "hierarchical_routing": False,
        }

        user_input = MagicMock()
        user_input.text = "padaryk šviesiau"
        user_input.language = "lt"
        user_input.conversation_id = "test_conv"
        user_input.context = MagicMock()

        no_match_res = MagicMock()
        no_match_res.response.response_type = IntentResponseType.ERROR
        no_match_res.response.data = {"code": "no_intent_match"}

        with unittest.mock.patch("custom_components.laya.conversation.conversation.async_converse", new_callable=AsyncMock) as mock_conv:
            mock_conv.return_value = no_match_res
            with unittest.mock.patch.object(entity, "_build_target_catalog") as mock_cat:
                mock_cat.return_value = (
                    {"Virtuvės šviesa": {"type": "entity", "id": "light.kitchen", "domain": "light"}},
                    {"kitchen": "Kitchen"},
                )
                res = await entity._async_process_internal(user_input)
                self.assertIsNotNone(res)
                mock_client.decide.assert_called_once()

    async def test_translation_translates_prompt_before_laya(self):
        """When translate_to_english is enabled, prompt is translated to English for Laya."""
        mock_client = MagicMock()
        decision_mock = MagicMock()
        decision_mock.action = DecisionChoice(choice="turn_off", confidence=0.99, probabilities={})
        decision_mock.target = DecisionChoice(choice="kitchen lights", confidence=0.99, probabilities={})
        mock_client.decide = AsyncMock(return_value=decision_mock)

        mock_hass = MagicMock()
        mock_hass.services.has_service.return_value = True
        mock_hass.services.async_call = AsyncMock()

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )
        entity.entry.options = {
            "try_default_agent_first": False,
            "translate_to_english": True,
            "hierarchical_routing": False,
        }

        user_input = MagicMock()
        user_input.text = "išjunk šviesą virtuvėj"
        user_input.language = "lt"
        user_input.conversation_id = "test_conv"
        user_input.context = MagicMock()

        with unittest.mock.patch(
            "custom_components.laya.conversation.async_translate_to_english",
            new_callable=AsyncMock,
        ) as mock_trans:
            mock_trans.return_value = "turn off the light in the kitchen"
            with unittest.mock.patch.object(entity, "_build_target_catalog") as mock_cat:
                mock_cat.return_value = (
                    {"kitchen lights": {"type": "entity", "id": "light.kitchen", "domain": "light"}},
                    {"kitchen": "Kitchen"},
                )
                with unittest.mock.patch.object(entity, "_build_result") as mock_build:
                    res = await entity._async_process_internal(user_input)
                    mock_trans.assert_called_once()
                    mock_client.decide.assert_called_once()
                    self.assertEqual(mock_client.decide.call_args.kwargs["command"], "turn off the light in the kitchen")
                    mock_build.assert_called_once()
                    self.assertEqual(mock_build.call_args[0][1], "Išjungta")

    async def test_whisper_phonetic_slip_atidaryg_vartos(self):
        """Whisper typo 'atidaryg vartos' is normalized and locked to open_cover."""
        mock_client = MagicMock()
        decision_mock = MagicMock()
        decision_mock.action = DecisionChoice(choice="turn_on", confidence=0.55, probabilities={})
        decision_mock.target = DecisionChoice(choice="Kiemo vartai", confidence=0.98, probabilities={})
        mock_client.decide = AsyncMock(return_value=decision_mock)

        mock_hass = MagicMock()
        mock_hass.services.has_service.return_value = True
        mock_hass.services.async_call = AsyncMock()

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )
        entity.entry.options = {
            "try_default_agent_first": False,
            "translate_to_english": False,
            "hierarchical_routing": False,
        }

        user_input = MagicMock()
        user_input.text = "atidaryg vartos"
        user_input.language = "lt"
        user_input.conversation_id = "test_conv"
        user_input.context = MagicMock()

        with unittest.mock.patch.object(entity, "_build_target_catalog") as mock_cat:
            mock_cat.return_value = (
                {"Kiemo vartai": {"type": "entity", "id": "cover.gate", "domain": "cover"}},
                {},
            )
            with unittest.mock.patch.object(entity, "_build_result") as mock_build:
                res = await entity._async_process_internal(user_input)
                self.assertEqual(mock_client.decide.call_args.kwargs["command"], "atidaryk vartos")
                mock_hass.services.async_call.assert_called_with(
                    "cover",
                    "open_cover",
                    service_data={"entity_id": "cover.gate"},
                    target={"entity_id": "cover.gate"},
                    blocking=True,
                )
                self.assertEqual(mock_build.call_args[0][1], "Atidaroma")

    async def test_isjunk_never_triggers_turn_on(self):
        """Typo 'isjung' or 'išjunk' overrides any neural confusion to turn_off."""
        mock_client = MagicMock()
        decision_mock = MagicMock()
        decision_mock.action = DecisionChoice(choice="turn_on", confidence=0.94, probabilities={})
        decision_mock.target = DecisionChoice(choice="Virtuvės šviesa", confidence=0.98, probabilities={})
        mock_client.decide = AsyncMock(return_value=decision_mock)

        mock_hass = MagicMock()
        mock_hass.services.has_service.return_value = True
        mock_hass.services.async_call = AsyncMock()

        entity = LayaConversationEntity(
            hass=mock_hass,
            entry=MagicMock(),
            client=mock_client,
        )
        entity.entry.options = {
            "try_default_agent_first": False,
            "translate_to_english": False,
            "hierarchical_routing": False,
        }

        user_input = MagicMock()
        user_input.text = "isjung sviesa virtuve"
        user_input.language = "lt"
        user_input.conversation_id = "test_conv"
        user_input.context = MagicMock()

        with unittest.mock.patch.object(entity, "_build_target_catalog") as mock_cat:
            mock_cat.return_value = (
                {"Virtuvės šviesa": {"type": "entity", "id": "light.kitchen", "domain": "light"}},
                {},
            )
            with unittest.mock.patch.object(entity, "_build_result") as mock_build:
                res = await entity._async_process_internal(user_input)
                mock_hass.services.async_call.assert_called_with(
                    "homeassistant",
                    "turn_off",
                    service_data={"entity_id": "light.kitchen"},
                    target={"entity_id": "light.kitchen"},
                    blocking=True,
                )
                self.assertEqual(mock_build.call_args[0][1], "Išjungta")

    def test_query_gate_state_returns_plural_atidaryti(self):
        """Covers and gates return natural plural form in Lithuanian (atidaryti / uždaryti)."""
        entity = LayaConversationEntity(hass=MagicMock(), entry=MagicMock(), client=MagicMock())
        mock_gate = MagicMock()
        mock_gate.domain = "cover"
        mock_gate.attributes = {}

        mock_gate.state = "open"
        res_open = entity._format_state_query_response("Kiemo vartai", mock_gate, "lt")
        self.assertEqual(res_open, "Kiemo vartai yra atidaryti")

        mock_gate.state = "closed"
        res_closed = entity._format_state_query_response("Kiemo vartai", mock_gate, "lt")
        self.assertEqual(res_closed, "Kiemo vartai yra uždaryti")

    def test_query_area_temperature_and_lights(self):
        """Querying an area intelligently reports temperature or light state based on utterance."""
        mock_hass = MagicMock()
        mock_temp_state = MagicMock()
        mock_temp_state.domain = "sensor"
        mock_temp_state.state = "21.5"
        mock_temp_state.attributes = {"unit_of_measurement": "°C", "device_class": "temperature"}
        mock_temp_state.entity_id = "sensor.kitchen_temperature"

        mock_light_state = MagicMock()
        mock_light_state.domain = "light"
        mock_light_state.state = "off"
        mock_light_state.attributes = {}
        mock_light_state.entity_id = "light.kitchen_ceiling"

        mock_hass.states.async_all.return_value = [mock_temp_state, mock_light_state]
        entity = LayaConversationEntity(hass=mock_hass, entry=MagicMock(), client=MagicMock())

        # 1. Ask for temperature
        res_temp = entity._query_area_state("kitchen", "Virtuvė", "lt", text="kokia temperatūra virtuvėje?")
        self.assertEqual(res_temp, "Virtuvė temperatūra yra 21.5 °C")

        # 2. Ask for lights
        res_light = entity._query_area_state("kitchen", "Virtuvė", "lt", text="ar virtuvėje įjungta šviesa?")
        self.assertEqual(res_light, "Virtuvė visos šviesos yra išjungtos")

        # 3. Ask for mower in garden
        mock_mower_state = MagicMock()
        mock_mower_state.domain = "lawn_mower"
        mock_mower_state.state = "mowing"
        mock_mower_state.attributes = {"friendly_name": "Žoliapjovė"}
        mock_mower_state.entity_id = "lawn_mower.garden_mower"
        mock_hass.states.async_all.return_value = [mock_mower_state]
        res_mower = entity._query_area_state("garden", "Sodas", "lt", text="ar sode žoliapjovė pjauna?")
        self.assertEqual(res_mower, "Žoliapjovė pjauna")

    def test_lawn_mower_deterministic_actions(self):
        """Verify lawn mower spoken commands are deterministically routed."""
        from custom_components.laya.conversation import _detect_deterministic_action
        self.assertEqual(_detect_deterministic_action("ar žoliapjovė pjauna?"), ("query_state", 1.0))
        self.assertEqual(_detect_deterministic_action("paleisk žoliapjovę"), ("start_mower", 1.0))
        self.assertEqual(_detect_deterministic_action("pjauk žolę"), ("start_mower", 1.0))
        self.assertEqual(_detect_deterministic_action("sustabdyk žoliapjovę"), ("pause_mower", 1.0))
        self.assertEqual(_detect_deterministic_action("grąžink žoliapjovę į stotelę"), ("dock_mower", 1.0))

    def test_device_level_aliases_registered_in_target_catalog(self):
        """Device-level aliases added in HA UI map directly to the primary lawn_mower entity."""
        mock_hass = MagicMock()

        # Mock mower state
        mock_mower_state = MagicMock()
        mock_mower_state.domain = "lawn_mower"
        mock_mower_state.entity_id = "lawn_mower.lidax_ultra_1200"
        mock_mower_state.attributes = {"friendly_name": "LiDAX Ultra 1200"}

        # Mock sensor on same device
        mock_battery_state = MagicMock()
        mock_battery_state.domain = "sensor"
        mock_battery_state.entity_id = "sensor.lidax_battery"
        mock_battery_state.attributes = {"friendly_name": "LiDAX Battery"}

        mock_hass.states.async_all.return_value = [mock_mower_state, mock_battery_state]

        # Entity registry entries pointing to device_1
        mock_mower_ent = MagicMock()
        mock_mower_ent.device_id = "device_1"
        mock_mower_ent.aliases = []

        mock_battery_ent = MagicMock()
        mock_battery_ent.device_id = "device_1"
        mock_battery_ent.aliases = []

        # Device registry entry with user aliases from screenshot
        mock_device = MagicMock()
        mock_device.id = "device_1"
        mock_device.name_by_user = None
        mock_device.aliases = ["Žolės pjovimo robotas", "Žolės robotas", "Žoliapjovė"]

        entity = LayaConversationEntity(hass=mock_hass, entry=MagicMock(), client=MagicMock())

        with unittest.mock.patch("custom_components.laya.conversation.entity_registry.async_get") as mock_ent_reg, \
             unittest.mock.patch("custom_components.laya.conversation.device_registry.async_get") as mock_dev_reg, \
             unittest.mock.patch("custom_components.laya.conversation.area_registry.async_get") as mock_area_reg:

            mock_area_reg.return_value.areas = {}
            mock_ent_reg.return_value.async_get.side_effect = lambda eid: mock_mower_ent if eid == "lawn_mower.lidax_ultra_1200" else mock_battery_ent
            mock_dev_reg.return_value.async_get.side_effect = lambda did: mock_device if did == "device_1" else None

            target_map, _ = entity._build_target_catalog(["lawn_mower", "sensor"])

            self.assertIn("Žoliapjovė", target_map)
            self.assertEqual(target_map["Žoliapjovė"]["id"], "lawn_mower.lidax_ultra_1200")
            self.assertEqual(target_map["Žoliapjovė"]["domain"], "lawn_mower")

            self.assertIn("Žolės robotas", target_map)
            self.assertEqual(target_map["Žolės robotas"]["id"], "lawn_mower.lidax_ultra_1200")

            self.assertIn("Žolės pjovimo robotas", target_map)
            self.assertEqual(target_map["Žolės pjovimo robotas"]["id"], "lawn_mower.lidax_ultra_1200")

    def test_lawn_mower_state_queries(self):
        """Lawn mower status queries format natural responses in Lithuanian."""
        entity = LayaConversationEntity(hass=MagicMock(), entry=MagicMock(), client=MagicMock())
        mock_mower = MagicMock()
        mock_mower.domain = "lawn_mower"
        mock_mower.attributes = {}

        mock_mower.state = "mowing"
        res_mowing = entity._format_state_query_response("Žoliapjovė", mock_mower, "lt")
        self.assertEqual(res_mowing, "Žoliapjovė pjauna")
        self.assertEqual(entity._format_state_query_response("Robotas", mock_mower, "lt"), "Robotas pjauna žolę")

        mock_mower.state = "docked"
        res_docked = entity._format_state_query_response("Žoliapjovė", mock_mower, "lt")
        self.assertEqual(res_docked, "Žoliapjovė yra stotelėje")

        mock_mower.state = "paused"
        res_paused = entity._format_state_query_response("Žoliapjovė", mock_mower, "lt")
        self.assertEqual(res_paused, "Žoliapjovė yra pristabdyta")

        mock_mower.state = "error"
        res_err = entity._format_state_query_response("Žoliapjovė", mock_mower, "lt")
        self.assertEqual(res_err, "Žoliapjovė: klaida")


if __name__ == "__main__":
    unittest.main()


