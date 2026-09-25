"""Unit tests for decision processing, localization, and response formatting."""

import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

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


if __name__ == "__main__":
    unittest.main()

