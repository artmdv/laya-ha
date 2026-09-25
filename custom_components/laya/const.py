"""Constants for the Laya System-1 Conversation integration."""

DOMAIN = "laya"
DEFAULT_NAME = "Laya System-1"

# Configuration Keys
CONF_URL = "url"
DEFAULT_URL = "http://localhost:8000"

CONF_API_KEY = "api_key"
DEFAULT_API_KEY = ""

CONF_TIMEOUT = "timeout"
DEFAULT_TIMEOUT = 3.0

CONF_CONFIDENCE_THRESHOLD = "confidence_threshold"
DEFAULT_CONFIDENCE_THRESHOLD = 0.50

CONF_EXPOSED_DOMAINS = "exposed_domains"
DEFAULT_EXPOSED_DOMAINS = [
    "light",
    "switch",
    "vacuum",
    "cover",
    "media_player",
    "climate",
    "fan",
]

AVAILABLE_DOMAINS = [
    "light",
    "switch",
    "vacuum",
    "cover",
    "media_player",
    "climate",
    "fan",
    "lock",
    "scene",
    "script",
]

CONF_RESPONSE_STYLE = "response_style"
STYLE_CONCISE = "concise"
STYLE_VERBOSE = "verbose"
DEFAULT_RESPONSE_STYLE = STYLE_CONCISE

# Standard actions mapped to Home Assistant services and human descriptions
ACTION_DEFINITIONS = {
    "turn_on": {
        "description": "Turn on a light, switch, fan, or appliance",
        "service": "homeassistant.turn_on",
    },
    "turn_off": {
        "description": "Turn off a light, switch, fan, or appliance",
        "service": "homeassistant.turn_off",
    },
    "toggle": {
        "description": "Toggle the power state of a light or device",
        "service": "homeassistant.toggle",
    },
    "open_cover": {
        "description": "Open a cover, curtain, blind, or garage door",
        "service": "cover.open_cover",
    },
    "close_cover": {
        "description": "Close a cover, curtain, blind, or garage door",
        "service": "cover.close_cover",
    },
    "start_vacuum": {
        "description": "Start cleaning with the robot vacuum cleaner",
        "service": "vacuum.start",
    },
    "stop_vacuum": {
        "description": "Stop or pause the robot vacuum cleaner",
        "service": "vacuum.pause",
    },
    "dock_vacuum": {
        "description": "Send the robot vacuum cleaner back to its dock or base",
        "service": "vacuum.return_to_base",
    },
    "media_play": {
        "description": "Play or resume media playback",
        "service": "media_player.media_play",
    },
    "media_pause": {
        "description": "Pause media playback",
        "service": "media_player.media_pause",
    },
}

# Localized concise confirmation responses
LOCALIZED_RESPONSES = {
    "en": {
        "turn_on": "Turned on",
        "turn_off": "Turned off",
        "toggle": "Toggled",
        "open_cover": "Opening",
        "close_cover": "Closing",
        "start_vacuum": "Vacuum started",
        "stop_vacuum": "Vacuum paused",
        "dock_vacuum": "Returning to dock",
        "media_play": "Playing",
        "media_pause": "Paused",
        "not_found": "Could not find a matching device",
        "low_confidence": "I'm not sure which device or action you mean",
        "error": "Sorry, an error occurred while executing the command",
        "done": "Done",
    },
    "lt": {
        "turn_on": "Įjungta",
        "turn_off": "Išjungta",
        "toggle": "Perjungta",
        "open_cover": "Atidaroma",
        "close_cover": "Uždaroma",
        "start_vacuum": "Siurblys pradėjo darbą",
        "stop_vacuum": "Siurblys sustabdytas",
        "dock_vacuum": "Siurblys grįžta krautis",
        "media_play": "Paleista",
        "media_pause": "Pristabdyta",
        "not_found": "Nerasta atitinkamo įrenginio",
        "low_confidence": "Nesu tikras, kurį įrenginį norite valdyti",
        "error": "Atsiprašau, įvyko ryšio arba vykdymo klaida",
        "done": "Atlikta",
    },
}
