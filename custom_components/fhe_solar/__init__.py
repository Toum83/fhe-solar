"""Intégration FHE Solar : production solaire et consommation via le portail FHE Smart."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .api import (
    DailyStat,
    FheAuthError,
    FheClient,
    FheConnectionError,
    overlay_energy_statistics,
)
from .const import (
    ATTR_END_DATE,
    ATTR_EXPORT_CAP_KWH,
    ATTR_PRICE_EXPORT,
    ATTR_PRICE_EXPORT_ABOVE,
    ATTR_PRICE_IMPORT,
    ATTR_START_DATE,
    CONF_CONTRACT_ANNIVERSARY,
    CONF_EXPORT_CAP_KWH,
    CONF_PRICE_EXPORT,
    CONF_PRICE_EXPORT_ABOVE,
    CONF_PRICE_IMPORT,
    DEFAULT_CONTRACT_ANNIVERSARY,
    DEFAULT_EXPORT_CAP_KWH,
    DEFAULT_PRICE_EXPORT,
    DEFAULT_PRICE_EXPORT_ABOVE,
    DEFAULT_PRICE_IMPORT,
    DOMAIN,
    SERVICE_WEEKLY_REPORT,
)
from .coordinator import FheCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type FheConfigEntry = ConfigEntry[FheCoordinator]

WEEKLY_REPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
        vol.Optional(ATTR_PRICE_IMPORT): vol.Coerce(float),
        vol.Optional(ATTR_PRICE_EXPORT): vol.Coerce(float),
        vol.Optional(ATTR_PRICE_EXPORT_ABOVE): vol.Coerce(float),
        vol.Optional(ATTR_EXPORT_CAP_KWH): vol.Coerce(float),
    }
)


def _contract_year_start(day: date, anniversary: str) -> date:
    """Début de l'année contractuelle (format MM-DD) contenant ``day``."""
    try:
        month, dom = (int(part) for part in anniversary.split("-", 1))
        candidate = date(day.year, month, dom)
    except (ValueError, TypeError):
        candidate = date(day.year, 1, 1)
    return candidate if candidate <= day else candidate.replace(year=day.year - 1)


async def async_setup_entry(hass: HomeAssistant, entry: FheConfigEntry) -> bool:
    """Configure une entrée FHE Solar."""
    # Session dédiée : le portail s'appuie sur un cookie PHPSESSID que l'on ne
    # veut pas mélanger avec le cookie jar partagé de Home Assistant.
    session = async_create_clientsession(hass)
    client = FheClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    try:
        await client.login()
    except FheAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except FheConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = FheCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FheConfigEntry) -> bool:
    """Décharge une entrée."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_WEEKLY_REPORT)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: FheConfigEntry) -> None:
    """Recharge l'entrée quand les options changent (intervalle, prix)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_overlay_ha_statistics(
    hass: HomeAssistant, entry: FheConfigEntry, days: list[DailyStat]
) -> list[DailyStat]:
    """Remplace les jours FHE par les statistiques longue durée de HA si dispo.

    Le portail FHE ne connaît pas les corrections faites dans HA (trou de
    données comblé après une panne, par exemple). Le bilan lit donc d'abord les
    statistiques des compteurs « du jour » de cette intégration, et ne retombe
    sur FHE que pour les jours que HA ne couvre pas.
    """
    if not days:
        return days
    registry = er.async_get(hass)
    entity_ids: dict[str, str] = {}
    for key in ("production_today", "grid_import_today", "grid_export_today"):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
        if entity_id is None:
            return days
        entity_ids[key] = entity_id

    start_dt = dt_util.start_of_local_day(days[0].day)
    end_dt = dt_util.start_of_local_day(days[-1].day + timedelta(days=1))
    try:
        stats = await get_instance(hass).async_add_executor_job(
            partial(
                statistics_during_period,
                hass,
                start_dt,
                end_dt,
                set(entity_ids.values()),
                "day",
                None,
                {"change"},
            )
        )
    except Exception:  # noqa: BLE001 - le bilan doit sortir même sans statistiques
        _LOGGER.warning("Statistiques HA indisponibles, bilan calculé avec les données FHE seules", exc_info=True)
        return days

    def _by_day(key: str) -> dict[date, float]:
        out: dict[date, float] = {}
        for row in stats.get(entity_ids[key], []):
            change = row.get("change")
            if change is None:
                continue
            day = dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date()
            out[day] = float(change)
        return out

    return overlay_energy_statistics(
        days,
        _by_day("production_today"),
        _by_day("grid_import_today"),
        _by_day("grid_export_today"),
        dt_util.now().date(),
    )


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_WEEKLY_REPORT):
        return

    async def _handle_weekly_report(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("Aucune entrée FHE Solar chargée")
        entry: FheConfigEntry = entries[0]
        coordinator = entry.runtime_data

        # Par défaut : la dernière semaine complète, du lundi au dimanche.
        today = dt_util.now().date()
        default_end = today - timedelta(days=today.weekday() + 1)  # dimanche dernier
        default_start = default_end - timedelta(days=6)
        start: date = call.data.get(ATTR_START_DATE, default_start)
        end: date = call.data.get(ATTR_END_DATE, default_end)

        price_import = call.data.get(
            ATTR_PRICE_IMPORT, entry.options.get(CONF_PRICE_IMPORT, DEFAULT_PRICE_IMPORT)
        )
        price_export = call.data.get(
            ATTR_PRICE_EXPORT, entry.options.get(CONF_PRICE_EXPORT, DEFAULT_PRICE_EXPORT)
        )
        price_export_above = call.data.get(
            ATTR_PRICE_EXPORT_ABOVE,
            entry.options.get(CONF_PRICE_EXPORT_ABOVE, DEFAULT_PRICE_EXPORT_ABOVE),
        )
        export_cap = call.data.get(
            ATTR_EXPORT_CAP_KWH, entry.options.get(CONF_EXPORT_CAP_KWH, DEFAULT_EXPORT_CAP_KWH)
        )
        anniversary = entry.options.get(CONF_CONTRACT_ANNIVERSARY, DEFAULT_CONTRACT_ANNIVERSARY)

        try:
            days = await coordinator.client.async_get_daily_stats(start, end)
            year_start = _contract_year_start(start, anniversary)
            # Injecté depuis le début de l'année contractuelle, hors période
            # analysée : donne le point de départ dans le barème à paliers.
            export_before = (
                await coordinator.client.async_get_export_kwh(year_start, start - timedelta(days=1))
                if start > year_start
                else 0.0
            )
        except FheAuthError as err:
            raise HomeAssistantError(f"Authentification FHE refusée : {err}") from err
        except FheConnectionError as err:
            raise HomeAssistantError(f"FHE injoignable : {err}") from err

        days = await _async_overlay_ha_statistics(hass, entry, days)

        prod = sum(d.production_kwh for d in days)
        conso = sum(d.consumption_kwh for d in days)
        self_kwh = sum(d.self_consumption_kwh for d in days)
        grid_import = sum(d.grid_import_kwh for d in days)
        grid_export = sum(d.grid_export_kwh for d in days)

        # Barème à paliers : le plafond annuel s'applique au cumulé de l'année
        # contractuelle, donc la semaine peut être à cheval sur les deux tarifs.
        remaining_cap = max(export_cap - export_before, 0.0)
        export_tier1 = min(grid_export, remaining_cap)
        export_tier2 = grid_export - export_tier1

        savings_import = self_kwh * price_import  # kWh non achetés au réseau
        revenue_export = export_tier1 * price_export + export_tier2 * price_export_above
        best_day = max(days, key=lambda d: d.production_kwh, default=None)

        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days_count": len(days),
            "days_from_ha_statistics": sum(1 for d in days if d.source == "ha_statistics"),
            "production_kwh": round(prod, 2),
            "consumption_kwh": round(conso, 2),
            "self_consumption_kwh": round(self_kwh, 2),
            "grid_import_kwh": round(grid_import, 2),
            "grid_export_kwh": round(grid_export, 2),
            "self_consumption_rate": round(100 * self_kwh / prod, 1) if prod else 0.0,
            "self_sufficiency_rate": round(100 * self_kwh / conso, 1) if conso else 0.0,
            "price_import": price_import,
            "price_export": price_export,
            "price_export_above": price_export_above,
            "export_cap_kwh": export_cap,
            "export_year_start": year_start.isoformat(),
            "export_before_period_kwh": round(export_before, 2),
            "export_at_tier1_kwh": round(export_tier1, 2),
            "export_at_tier2_kwh": round(export_tier2, 2),
            "export_cap_remaining_kwh": round(max(remaining_cap - export_tier1, 0.0), 2),
            "grid_cost_eur": round(grid_import * price_import, 2),
            "savings_import_eur": round(savings_import, 2),
            "revenue_export_eur": round(revenue_export, 2),
            "savings_total_eur": round(savings_import + revenue_export, 2),
            "best_day": best_day.day.isoformat() if best_day else None,
            "best_day_production_kwh": best_day.production_kwh if best_day else None,
            "days": [
                {
                    "date": d.day.isoformat(),
                    "production_kwh": d.production_kwh,
                    "consumption_kwh": d.consumption_kwh,
                    "self_consumption_kwh": d.self_consumption_kwh,
                    "grid_import_kwh": d.grid_import_kwh,
                    "grid_export_kwh": d.grid_export_kwh,
                    "self_consumption_rate": d.self_consumption_rate,
                    "self_sufficiency_rate": d.self_sufficiency_rate,
                    "source": d.source,
                }
                for d in days
            ],
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_WEEKLY_REPORT,
        _handle_weekly_report,
        schema=WEEKLY_REPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
