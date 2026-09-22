"""Config flow FHE Solar."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import FheAuthError, FheClient, FheConnectionError
from .const import (
    CONF_CONTRACT_ANNIVERSARY,
    CONF_EXPORT_CAP_KWH,
    CONF_PRICE_EXPORT,
    CONF_PRICE_EXPORT_ABOVE,
    CONF_PRICE_IMPORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONTRACT_ANNIVERSARY,
    DEFAULT_EXPORT_CAP_KWH,
    DEFAULT_PRICE_EXPORT,
    DEFAULT_PRICE_EXPORT_ABOVE,
    DEFAULT_PRICE_IMPORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class FheConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flux de configuration : identifiants FHE Smart."""

    VERSION = 1

    async def _async_validate(self, username: str, password: str) -> str | None:
        """Teste la connexion ; renvoie une clé d'erreur ou None.

        Utilise la session HTTP partagée de Home Assistant (``async_get_clientsession``)
        et ne la ferme jamais : c'est une ressource gérée par HA, la fermer ici casserait
        les requêtes suivantes (y compris celles du setup réel de l'entrée).
        """
        session = async_get_clientsession(self.hass)
        client = FheClient(session, username, password)
        try:
            await client.login()
        except FheAuthError:
            return "invalid_auth"
        except FheConnectionError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Erreur inattendue pendant la validation FHE")
            return "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            error = await self._async_validate(username, user_input[CONF_PASSWORD])
            if error is None:
                return self.async_create_entry(
                    title=f"FHE Solar ({username})",
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
            errors["base"] = error

        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            error = await self._async_validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FheOptionsFlow:
        return FheOptionsFlow()


class FheOptionsFlow(OptionsFlow):
    """Options : intervalle de rafraîchissement et prix du kWh."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PRICE_IMPORT,
                    default=options.get(CONF_PRICE_IMPORT, DEFAULT_PRICE_IMPORT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=2, step=0.0001, unit_of_measurement="€/kWh",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PRICE_EXPORT,
                    default=options.get(CONF_PRICE_EXPORT, DEFAULT_PRICE_EXPORT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=2, step=0.0001, unit_of_measurement="€/kWh",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PRICE_EXPORT_ABOVE,
                    default=options.get(CONF_PRICE_EXPORT_ABOVE, DEFAULT_PRICE_EXPORT_ABOVE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=2, step=0.0001, unit_of_measurement="€/kWh",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_EXPORT_CAP_KWH,
                    default=options.get(CONF_EXPORT_CAP_KWH, DEFAULT_EXPORT_CAP_KWH),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100000, step=1, unit_of_measurement="kWh/an",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_CONTRACT_ANNIVERSARY,
                    default=options.get(CONF_CONTRACT_ANNIVERSARY, DEFAULT_CONTRACT_ANNIVERSARY),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
