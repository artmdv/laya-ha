"""Config flow for Laya System-1 Conversation integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .client import LayaClient, LayaConnectionError, LayaTimeoutError
from .const import (
    AVAILABLE_DOMAINS,
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
    CONF_URL,
    DEFAULT_API_KEY,
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
    DEFAULT_URL,
    DOMAIN,
    STYLE_CONCISE,
    STYLE_VERBOSE,
)

from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)

OptionsFlowBase = getattr(
    config_entries, "OptionsFlowWithConfigEntry", config_entries.OptionsFlow
)


class LayaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Laya System-1 Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            api_key = user_input.get(CONF_API_KEY, "").strip()

            session = async_get_clientsession(self.hass)
            client = LayaClient(base_url=url, api_key=api_key, session=session)

            try:
                is_healthy = await client.check_health()
                if not is_healthy:
                    errors["base"] = "cannot_connect"
            except (LayaConnectionError, LayaTimeoutError):
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error testing Laya server: %s", err)
                errors["base"] = "unknown"

            if not errors:
                await self.async_set_unique_id(f"laya_{url}")
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input.get("name", DEFAULT_NAME),
                    data={
                        CONF_URL: url,
                        CONF_API_KEY: api_key,
                    },
                    options={
                        CONF_CONFIDENCE_THRESHOLD: DEFAULT_CONFIDENCE_THRESHOLD,
                        CONF_EXPOSED_DOMAINS: DEFAULT_EXPOSED_DOMAINS,
                        CONF_RESPONSE_STYLE: DEFAULT_RESPONSE_STYLE,
                        CONF_TIMEOUT: DEFAULT_TIMEOUT,
                        CONF_HIERARCHICAL_ROUTING: DEFAULT_HIERARCHICAL_ROUTING,
                        CONF_DEBUG_LOGGING: DEFAULT_DEBUG_LOGGING,
                        CONF_TRY_DEFAULT_AGENT_FIRST: DEFAULT_TRY_DEFAULT_AGENT_FIRST,
                        CONF_TRANSLATE_TO_ENGLISH: DEFAULT_TRANSLATE_TO_ENGLISH,
                        CONF_TRANSLATION_URL: DEFAULT_TRANSLATION_URL,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=DEFAULT_URL): cv.string,
                vol.Optional(CONF_API_KEY, default=DEFAULT_API_KEY): cv.string,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return LayaOptionsFlowHandler(config_entry)


class LayaOptionsFlowHandler(OptionsFlowBase):
    """Handle options flow for Laya System-1."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        try:
            super().__init__(config_entry)
        except (TypeError, AttributeError):
            super().__init__()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Laya options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = getattr(self, "options", None) or self._config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CONFIDENCE_THRESHOLD,
                        default=options.get(
                            CONF_CONFIDENCE_THRESHOLD, DEFAULT_CONFIDENCE_THRESHOLD
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.1,
                            max=1.0,
                            step=0.05,
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Optional(
                        CONF_EXPOSED_DOMAINS,
                        default=options.get(
                            CONF_EXPOSED_DOMAINS, DEFAULT_EXPOSED_DOMAINS
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=AVAILABLE_DOMAINS,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_RESPONSE_STYLE,
                        default=options.get(
                            CONF_RESPONSE_STYLE, DEFAULT_RESPONSE_STYLE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=STYLE_CONCISE,
                                    label="Concise (e.g. 'Turned off' / 'Išjungta')",
                                ),
                                selector.SelectOptionDict(
                                    value=STYLE_VERBOSE,
                                    label="Verbose (e.g. 'Turned off Living Room Light')",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_HIERARCHICAL_ROUTING,
                        default=options.get(
                            CONF_HIERARCHICAL_ROUTING, DEFAULT_HIERARCHICAL_ROUTING
                        ),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_DEBUG_LOGGING,
                        default=options.get(
                            CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING
                        ),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_TRY_DEFAULT_AGENT_FIRST,
                        default=options.get(
                            CONF_TRY_DEFAULT_AGENT_FIRST, DEFAULT_TRY_DEFAULT_AGENT_FIRST
                        ),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_TRANSLATE_TO_ENGLISH,
                        default=options.get(
                            CONF_TRANSLATE_TO_ENGLISH, DEFAULT_TRANSLATE_TO_ENGLISH
                        ),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_TRANSLATION_URL,
                        default=options.get(
                            CONF_TRANSLATION_URL, DEFAULT_TRANSLATION_URL
                        ),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.URL,
                        )
                    ),
                    vol.Optional(
                        CONF_TIMEOUT,
                        default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1.0,
                            max=30.0,
                            step=0.5,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
        )

