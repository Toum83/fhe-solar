"""Statistique « Gaz GRDF (jour réel) » à partir de l'attribut daily de l'intégration Gazpar."""

from __future__ import annotations

from datetime import timedelta
from functools import partial
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .gas_days import cumulative_rows, parse_daily, stamp

_LOGGER = logging.getLogger(__name__)

GAS_STATISTIC_ID = f"{DOMAIN}:gaz_grdf_jour_reel"
# Le tableau Énergie refuse un prix fixe sur une statistique externe : il lui faut une statistique de coût.
GAS_COST_STATISTIC_ID = f"{DOMAIN}:gaz_grdf_jour_reel_cout"


def _metadata(statistic_id: str, name: str, unit: str, unit_class: str | None) -> dict:
    meta = {
        "has_mean": False,
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    try:  # HA 2025.x+ : type de moyenne et classe d'unité explicites
        from homeassistant.components.recorder.models import StatisticMeanType

        meta["mean_type"] = StatisticMeanType.NONE
        meta["unit_class"] = unit_class
    except ImportError:
        pass
    return meta


async def _base_sum(hass: HomeAssistant, statistic_id: str, before) -> float:
    """Cumul de la statistique juste avant ``before`` (dernière ligne des 400 jours précédents)."""
    stats = await get_instance(hass).async_add_executor_job(
        partial(
            statistics_during_period,
            hass,
            before - timedelta(days=400),
            before,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
    )
    previous = stats.get(statistic_id, [])
    return float(previous[-1]["sum"] or 0.0) if previous else 0.0


async def async_sync_gas(hass: HomeAssistant, entity_id: str, price: float) -> int:
    """Réécrit les jours publiés par Gazpar à leur vraie date. Renvoie le nombre de jours écrits."""
    state = hass.states.get(entity_id)
    days = parse_daily(state.attributes.get("daily") if state else None)
    if not days:
        return 0
    tz = dt_util.get_default_time_zone()
    first = stamp(min(days), tz)
    for statistic_id, name, unit, unit_class, values in (
        (GAS_STATISTIC_ID, "Gaz GRDF (jour réel)", UnitOfEnergy.KILO_WATT_HOUR, "energy", days),
        (GAS_COST_STATISTIC_ID, "Gaz GRDF (jour réel) — coût", "EUR", None, {d: v * price for d, v in days.items()}),
    ):
        rows = cumulative_rows(values, await _base_sum(hass, statistic_id, first), tz)
        async_add_external_statistics(
            hass,
            _metadata(statistic_id, name, unit, unit_class),
            [{"start": start, "sum": total, "state": value} for start, total, value in rows],
        )
    _LOGGER.debug("Gaz jour réel : %d jours écrits (%s → %s)", len(days), min(days), max(days))
    return len(days)


def async_setup_gas_real_day(hass: HomeAssistant, entity_id: str, price: float):
    """Suit le capteur Gazpar ; renvoie la fonction de désabonnement."""

    async def _sync() -> None:
        try:
            await async_sync_gas(hass, entity_id, price)
        except Exception:  # noqa: BLE001 - ne jamais casser l'intégration pour le gaz
            _LOGGER.warning("Gaz jour réel : écriture des statistiques impossible", exc_info=True)

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        hass.async_create_task(_sync())

    hass.async_create_task(_sync())
    return async_track_state_change_event(hass, [entity_id], _changed)
