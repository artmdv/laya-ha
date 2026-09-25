"""The Laya System-1 Conversation integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import LayaClient
from .const import CONF_API_KEY, CONF_TIMEOUT, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Laya System-1 Conversation from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    base_url = entry.data[CONF_URL]
    api_key = entry.data.get(CONF_API_KEY)
    timeout = entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

    session = async_get_clientsession(hass)
    client = LayaClient(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        session=session,
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
    }

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, {})
        client: LayaClient | None = data.get("client")
        if client:
            await client.close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)
