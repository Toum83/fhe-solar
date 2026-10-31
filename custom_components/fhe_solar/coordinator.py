"""Coordinateur de rafraîchissement FHE Solar."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import DashboardData, FheAuthError, FheClient, FheConnectionError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_POWER_STEP
from .hour_lag import HourLagCompensator
from .steps import detect_steps, parse_serie

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
        self._lag = HourLagCompensator()
        # Dernier point de la courbe à 7 s déjà traité : gardé au redémarrage pour rattraper
        # l'heure que FHE conserve, sans renvoyer deux fois les mêmes échelons.
        self._steps_store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.power_steps")
        self._steps_after_ms: int | None = None
        self._steps_loaded = False

    async def _async_update_data(self) -> DashboardData:
        try:
            data = await self.client.async_get_dashboard()
        except FheAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FheConnectionError as err:
            raise UpdateFailed(str(err)) from err

        now = dt_util.now()
        # FHE publie ses totaux du jour avec une heure de retard : on y ajoute l'heure en
        # cours, intégrée depuis les puissances (voir hour_lag.py).
        for name, value in self._lag.update(
            now,
            data.production_today,
            data.consumption_today,
            data.self_consumption_today,
            data.consumption_power,
            data.production_power,
        ).items():
            setattr(data, name, value)

        today = now.date()
        previous = self.data
        # Juste après minuit, FHE peut encore renvoyer les totaux de la veille au premier
        # relevé : sa remise à zéro qui suit est légitime et ne doit pas être bloquée.
        midnight_reset = now.hour == 0 and now.minute < 15
        if previous is not None and self._last_day == today and not midnight_reset:
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
        await self._async_fire_power_steps(data)
        return data

    async def _async_fire_power_steps(self, data: DashboardData) -> None:
        """Envoie un événement HA par échelon nouveau de la courbe de consommation à 7 s."""
        lines = {
            f"{line.get('drive_elec_mac')}_{line.get('code')}": line
            for line in (data.raw or {}).get("drive_elec_lines") or []
        }
        conso = parse_serie((lines.get(data.consumption_clamp_id) or {}).get("serie"))
        if not conso:
            return
        prod = dict(parse_serie((lines.get(data.production_clamp_id) or {}).get("serie")))
        if not self._steps_loaded:
            stored = await self._steps_store.async_load() or {}
            self._steps_after_ms = stored.get("after_ms")
            self._steps_loaded = True
        # Les 2 derniers points attendent le relevé suivant : une pointe ou un échelon étalé ne se
        # juge qu'avec les points qui suivent.
        cutoff = conso[-3][0] if len(conso) >= 3 else conso[0][0]
        if self._steps_after_ms is None:
            # Toute première fois : on part de maintenant, sans renvoyer l'heure passée.
            self._steps_after_ms = cutoff
        else:
            for step in detect_steps(conso, self._steps_after_ms, prod):
                if step.ts_ms > cutoff:
                    break
                self.hass.bus.async_fire(
                    EVENT_POWER_STEP,
                    {
                        "ts": dt_util.utc_from_timestamp(step.ts_ms / 1000).isoformat(),
                        "power_before": step.power_before,
                        "power_after": step.power_after,
                        "delta_w": step.delta_w,
                        "direction": "on" if step.delta_w > 0 else "off",
                        "production_power": step.production_power,
                    },
                )
            self._steps_after_ms = max(self._steps_after_ms, cutoff)
        self._steps_store.async_delay_save(lambda: {"after_ms": self._steps_after_ms}, 10)
