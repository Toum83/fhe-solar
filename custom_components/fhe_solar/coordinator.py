"""Coordinateur de rafraîchissement FHE Solar."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util

from .api import DashboardData, FheAuthError, FheClient, FheConnectionError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Champs kWh "du jour" protégés contre une baisse parasite hors changement de jour.
_DAILY_FIELDS = (
    "production_today",
    "consumption_today",
    "self_consumption_today",
    "grid_import_today",
    "grid_export_today",
)


class FheCoordinator(DataUpdateCoordinator[DashboardData]):
    """Interroge le tableau de bord FHE à intervalle régulier."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: FheClient) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.data.get('username')})",
            update_interval=timedelta(minutes=minutes),
            config_entry=entry,
        )
        self.client = client
        self._last_day = None

    async def _async_update_data(self) -> DashboardData:
        try:
            data = await self.client.async_get_dashboard()
        except FheAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FheConnectionError as err:
            raise UpdateFailed(str(err)) from err

        today = dt_util.now().date()
        previous = self.data
        if previous is not None and self._last_day == today:
            # Les compteurs "du jour" sont en state_class total_increasing :
            # HA interpréterait une baisse comme une remise à zéro et gonflerait
            # les statistiques. On ignore donc toute baisse tant que le jour
            # n'a pas changé.
            for field in _DAILY_FIELDS:
                new, old = getattr(data, field), getattr(previous, field)
                if new < old:
                    _LOGGER.debug(
                        "%s a baissé (%s → %s) sans changement de jour, valeur conservée",
                        field, old, new,
                    )
                    setattr(data, field, old)
        self._last_day = today
        return data
