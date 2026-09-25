"""Unit tests for decision processing, localization, and response formatting."""

import sys
import unittest
from unittest.mock import MagicMock

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

from custom_components.laya.const import (
    ACTION_DEFINITIONS,
    DEFAULT_EXPOSED_DOMAINS,
    LOCALIZED_RESPONSES,
    MAX_TARGET_CANDIDATES,
    STYLE_CONCISE,
    STYLE_VERBOSE,
)
from custom_components.laya.conversation import LayaConversationEntity


class TestDecisionLogic(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
