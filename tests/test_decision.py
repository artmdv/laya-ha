"""Unit tests for decision processing, localization, and response formatting."""

import sys
import unittest
from unittest.mock import MagicMock

if "homeassistant" not in sys.modules:
    ha_mock = MagicMock()
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
    STYLE_CONCISE,
    STYLE_VERBOSE,
)


class TestDecisionLogic(unittest.TestCase):
    """Test suite for action mappings, localization, and target resolution."""

    def test_all_actions_have_descriptions_and_services(self):
        """Verify all defined actions have valid service and description strings."""
        for action_name, action_data in ACTION_DEFINITIONS.items():
            self.assertIn("description", action_data, f"Missing description for {action_name}")
            self.assertIn("service", action_data, f"Missing service for {action_name}")
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


if __name__ == "__main__":
    unittest.main()
