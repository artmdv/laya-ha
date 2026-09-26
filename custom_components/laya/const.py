"""Constants for the Laya System-1 Conversation integration."""

DOMAIN = "laya"
DEFAULT_NAME = "Laya System-1"

# Configuration Keys
CONF_URL = "url"
DEFAULT_URL = "http://localhost:8000"

CONF_API_KEY = "api_key"
DEFAULT_API_KEY = ""

CONF_TIMEOUT = "timeout"
DEFAULT_TIMEOUT = 10.0

CONF_CONFIDENCE_THRESHOLD = "confidence_threshold"
DEFAULT_CONFIDENCE_THRESHOLD = 0.50

# Maximum candidates sent to Laya to stay safely under head_max_len=256 and speed up inference
MAX_TARGET_CANDIDATES = 100

CONF_EXPOSED_DOMAINS = "exposed_domains"
DEFAULT_EXPOSED_DOMAINS = [
    "light",
    "switch",
    "vacuum",
    "cover",
    "sensor",
    "binary_sensor",
    "media_player",
    "climate",
    "fan",
]

AVAILABLE_DOMAINS = [
    "light",
    "switch",
    "vacuum",
    "cover",
    "sensor",
    "binary_sensor",
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

CONF_HIERARCHICAL_ROUTING = "hierarchical_routing"
DEFAULT_HIERARCHICAL_ROUTING = True

CONF_DEBUG_LOGGING = "debug_logging"
DEFAULT_DEBUG_LOGGING = False

CONF_TRY_DEFAULT_AGENT_FIRST = "try_default_agent_first"
DEFAULT_TRY_DEFAULT_AGENT_FIRST = True

CONF_TRANSLATE_TO_ENGLISH = "translate_to_english"
DEFAULT_TRANSLATE_TO_ENGLISH = True

CONF_TRANSLATION_URL = "translation_url"
DEFAULT_TRANSLATION_URL = ""

# Multilingual area synonyms and stems to map spoken rooms to configured area names
AREA_SYNONYMS: dict[str, list[str]] = {
    "kitchen": [
        "virtuv", "virtuve", "virtuvė", "virtuvės", "virtuvėj", "virtuveje", "virtuvėje",
        "kuchnia", "küche", "cuisine", "cocina",
    ],
    "living_room": [
        "svetain", "svetainė", "svetainės", "svetainėj", "svetaineje", "svetainėje",
        "salonas", "salono", "salone", "saloną",
        "salon", "living", "pokój dzienny", "wohnzimmer",
    ],
    "bedroom": [
        "miegam", "miegamasis", "miegamojo", "miegamajame",
        "sypialnia", "schlafzimmer", "dormitorio", "chambre",
    ],
    "bathroom": [
        "voni", "vonia", "vonios", "vonioje", "vonion", "tualet", "tualetas", "tualete", "wc",
        "łazienka", "bad", "badezimmer", "baño", "salle de bain",
    ],
    "corridor": [
        "koridor", "koridorius", "koridoriuje", "koridoriuj",
        "prieškambar", "prieškambaris", "prieškambaryje", "hol", "holas", "hole",
        "hallway", "corridor", "flur", "przedpokój",
    ],
    "hallway": [
        "koridor", "koridorius", "koridoriuje", "koridoriuj",
        "prieškambar", "prieškambaris", "prieškambaryje", "hol", "holas", "hole",
        "hallway", "corridor", "flur", "przedpokój",
    ],
    "office": [
        "darbo", "darbo kambarys", "kabinet", "kabinetas", "kabinete",
        "biuras", "biure", "office", "study", "büro", "gabinet",
    ],
    "kids_room": [
        "vaik", "vaikų", "vaiku", "vaiko", "vaikų kambarys", "vaikų kambaryje",
        "children", "kids", "pokój dziecięcy", "kinderzimmer",
    ],
    "outdoor": [
        "lauk", "laukas", "lauko", "lauke",
        "kiem", "kiemas", "kiemo", "kieme",
        "sod", "sodas", "sodo", "sode",
        "yard", "garden", "outdoor", "outside", "ogród", "garten",
    ],
    "garage": [
        "garaž", "garažas", "garažo", "garaže", "garaz", "garage",
    ],
    "balcony": [
        "balkon", "balkonas", "balkono", "balkone", "balcony",
    ],
    "terrace": [
        "teras", "terasa", "terasos", "terasoje", "terrace",
    ],
    "basement": [
        "rūs", "rūsys", "rūsio", "rūsyje", "basement", "cellar", "piwnica", "keller",
    ],
}

# Standard actions mapped to Home Assistant services and human descriptions
ACTION_DEFINITIONS = {
    "turn_on": {
        "description": "Command to turn on, switch on, or activate a light, switch, fan, or device (komanda įjungti, uždegti, paleisti, pvz. 'įjunk šviesą', 'uždek lemputę'). Imperative command, never for questions starting with 'ar'",
        "service": "homeassistant.turn_on",
    },
    "turn_off": {
        "description": "Command to turn off, switch off, or deactivate a light, switch, fan, or device (komanda išjungti, užgesinti, sustabdyti, pvz. 'išjunk šviesą', 'užgesink'). Imperative command, never for questions starting with 'ar'",
        "service": "homeassistant.turn_off",
    },
    "toggle": {
        "description": "Toggle the power state of a light or device (command to toggle state, e.g. perjunk)",
        "service": "homeassistant.toggle",
    },
    "open_cover": {
        "description": "Open, raise a cover, curtain, blind, gate, barrier, or garage door (command to open, e.g. atidaryk, pakelk, atkėl)",
        "service": "cover.open_cover",
    },
    "close_cover": {
        "description": "Close, lower a cover, curtain, blind, gate, barrier, or garage door (command to close, e.g. uždaryk, nuleisk, užverk)",
        "service": "cover.close_cover",
    },
    "start_vacuum": {
        "description": "Start cleaning with the robot vacuum cleaner (command to vacuum, e.g. paleisk siurblį, siurbk)",
        "service": "vacuum.start",
    },
    "stop_vacuum": {
        "description": "Stop or pause the robot vacuum cleaner (command to stop vacuum, e.g. sustabdyk siurblį)",
        "service": "vacuum.pause",
    },
    "dock_vacuum": {
        "description": "Send the robot vacuum cleaner back to its dock or base (command to return vacuum to dock, e.g. grąžink siurblį į krovimo stotelę)",
        "service": "vacuum.return_to_base",
    },
    "media_play": {
        "description": "Play or resume media playback (command to play media, e.g. groti, paleisk muziką)",
        "service": "media_player.media_play",
    },
    "media_pause": {
        "description": "Pause media playback (command to pause media, e.g. pauzė, sustabdyk muziką)",
        "service": "media_player.media_pause",
    },
    "query_state": {
        "description": "Question asking whether a device or light is on, off, open, closed, or asking about temperature or measurement (klausimas ar įjungta, ar išjungta, ar atidaryta, ar uždaryta, ar šviesa įjungta, kokia temperatūra, koks statusas). Any question starting with 'ar', 'kokia', 'koks'",
        "service": None,
    },
    "no_action": {
        "description": "None of the above, unhandled command, conversation, test, greeting, or irrelevant text (nieko nedaryti, nesusijęs tekstas, bandymas, pasisveikinimas, neaiški komanda)",
        "service": None,
    },
}

# Domains compatible with specific actions to prevent nonsensical executions
ACTION_COMPATIBLE_DOMAINS: dict[str, set[str] | None] = {
    "turn_on": {"light", "switch", "fan", "climate", "media_player", "vacuum", "area"},
    "turn_off": {"light", "switch", "fan", "climate", "media_player", "vacuum", "area"},
    "toggle": {"light", "switch", "fan", "area"},
    "open_cover": {"cover", "area"},
    "close_cover": {"cover", "area"},
    "start_vacuum": {"vacuum", "area"},
    "stop_vacuum": {"vacuum", "area"},
    "dock_vacuum": {"vacuum", "area"},
    "media_play": {"media_player", "area"},
    "media_pause": {"media_player", "area"},
    "query_state": None,
    "no_action": None,
}

LOCALIZED_STATES = {
    "en": {
        "on": "on",
        "off": "off",
        "open": "open",
        "closed": "closed",
        "locked": "locked",
        "unlocked": "unlocked",
        "cleaning": "cleaning",
        "docked": "docked",
        "paused": "paused",
        "idle": "idle",
        "unavailable": "unavailable",
        "unknown": "unknown",
        "is": "is",
    },
    "lt": {
        "on": "įjungta",
        "off": "išjungta",
        "open": "atidaryta",
        "closed": "uždaryta",
        "locked": "užrakinta",
        "unlocked": "atrakinta",
        "cleaning": "valo",
        "docked": "kraunasi stotelėje",
        "paused": "pristabdytas",
        "idle": "laukia",
        "unavailable": "nepasiekiamas",
        "unknown": "nežinoma",
        "is": "yra",
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
        "no_action": "I didn't understand the command",
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
        "no_action": "Nesupratau komandos",
        "error": "Atsiprašau, įvyko ryšio arba vykdymo klaida",
        "done": "Atlikta",
    },
}
