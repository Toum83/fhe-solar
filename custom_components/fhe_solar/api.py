"""Client HTTP pour le portail FHE Smart (fhesmart.fhe-france.com).

API non documentée, reconstituée à partir du trafic de l'application web :

- ``POST /login`` avec ``username`` (ou ``email``), ``password``, ``remember``
  → JSON ``{"success": bool, "redirect_url": str, "message": str}``.
  La session est portée par le cookie ``PHPSESSID``.
- ``GET /dashboard/data/all_widgets`` → puissances instantanées (W) des pinces
  et totaux du jour (kWh) : prod, conso, autoconso, réseau, surplus.
- ``GET /stats/data/json?date=<unix>&pas=hours|days|months`` → historique
  (Wh) par heure du jour / jour du mois / mois de l'année contenant ``date``,
  avec taux d'autoproduction / autoconsommation.

Une requête faite sans session valide répond ``302`` vers ``/``.

Ce module ne dépend pas de Home Assistant : il est réutilisable tel quel dans
un script ou une autre application.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
import re
from typing import Any

import aiohttp

BASE_URL = "https://fhesmart.fhe-france.com"

_LOGGER = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[a-z][.a-z0-9_+-]*@[a-z][a-z0-9_-]*\.[a-z]{2,4}", re.I)


class FheError(Exception):
    """Erreur générique FHE."""


class FheAuthError(FheError):
    """Identifiants refusés par le portail."""


class FheConnectionError(FheError):
    """Portail injoignable ou réponse inattendue."""


@dataclass
class DashboardData:
    """Instantané du tableau de bord FHE."""

    production_power: float | None  # W
    consumption_power: float | None  # W
    production_today: float  # kWh
    consumption_today: float  # kWh
    self_consumption_today: float  # kWh
    grid_import_today: float  # kWh
    grid_export_today: float  # kWh
    forecast_today: float | None
    forecast_tomorrow: float | None
    production_clamp_id: str | None
    consumption_clamp_id: str | None
    device_mac: str | None
    device_label: str | None
    last_sample: datetime | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def self_consumption_rate(self) -> float | None:
        """Part de la production consommée sur place (%)."""
        if self.production_today <= 0:
            return None
        return round(100 * self.self_consumption_today / self.production_today, 1)

    @property
    def self_sufficiency_rate(self) -> float | None:
        """Part de la consommation couverte par le solaire (%)."""
        if self.consumption_today <= 0:
            return None
        return round(100 * self.self_consumption_today / self.consumption_today, 1)


@dataclass
class DailyStat:
    """Bilan d'une journée."""

    day: date
    production_kwh: float
    consumption_kwh: float
    self_consumption_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    self_consumption_rate: float | None
    self_sufficiency_rate: float | None


class FheClient:
    """Client asynchrone pour FHE Smart."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = BASE_URL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._login_lock = asyncio.Lock()
        self._logged_in = False

    # ------------------------------------------------------------------ auth

    async def login(self) -> None:
        """Ouvre une session (cookie PHPSESSID)."""
        key = "email" if _EMAIL_RE.match(self._username) else "username"
        payload = {key: self._username, "password": self._password, "remember": "true"}
        try:
            # Même séquence que le navigateur : la page d'accueil ouvre la
            # session PHP (cookie PHPSESSID) avant l'appel de login.
            async with self._session.get(
                f"{self._base_url}/", timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    raise FheConnectionError(f"Accueil HTTP {resp.status}")
            async with self._session.post(
                f"{self._base_url}/checkCookies",
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=aiohttp.ClientTimeout(total=20),
            ):
                pass
            async with self._session.post(
                f"{self._base_url}/login",
                data=payload,
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": f"{self._base_url}/",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    raise FheConnectionError(f"Login HTTP {resp.status}")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise FheConnectionError(f"Login impossible: {err}") from err
        except ValueError as err:
            raise FheConnectionError("Réponse de login non JSON") from err

        if not body.get("success"):
            raise FheAuthError(body.get("message") or "Identifiants refusés")
        self._logged_in = True
        _LOGGER.debug("Session FHE ouverte pour %s", self._username)

    async def _ensure_login(self) -> None:
        async with self._login_lock:
            if not self._logged_in:
                await self.login()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET JSON authentifié, avec une reconnexion automatique si la session a expiré."""
        await self._ensure_login()
        for attempt in (1, 2):
            try:
                async with self._session.get(
                    f"{self._base_url}{path}",
                    params=params,
                    allow_redirects=False,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status in (301, 302, 401, 403):
                        # Session expirée : on se reconnecte une fois.
                        self._logged_in = False
                        if attempt == 1:
                            _LOGGER.debug("Session FHE expirée, reconnexion")
                            await self._ensure_login()
                            continue
                        raise FheAuthError("Session refusée après reconnexion")
                    if resp.status != 200:
                        raise FheConnectionError(f"GET {path} → HTTP {resp.status}")
                    ctype = resp.headers.get("Content-Type", "")
                    if "json" not in ctype:
                        raise FheConnectionError(f"GET {path} → réponse non JSON ({ctype})")
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise FheConnectionError(f"GET {path}: {err}") from err
        raise FheConnectionError(f"GET {path}: échec inattendu")

    # ------------------------------------------------------------- dashboard

    async def async_get_dashboard(self) -> DashboardData:
        """Récupère puissances instantanées et totaux du jour."""
        raw = await self._get_json("/dashboard/data/all_widgets")
        return _parse_dashboard(raw)

    # ----------------------------------------------------------------- stats

    async def async_get_stats(self, when: date | datetime, step: str) -> dict[str, Any]:
        """Statistiques brutes pour ``step`` ∈ {hours, days, months}.

        ``when`` : n'importe quel instant du jour / mois / année voulu.
        """
        if step not in ("hours", "days", "months"):
            raise ValueError("step doit valoir hours, days ou months")
        if isinstance(when, datetime):
            ts = int(when.timestamp())
        else:
            ts = int(datetime(when.year, when.month, when.day, 12).timestamp())
        raw = await self._get_json("/stats/data/json", {"date": ts, "pas": step})
        return raw.get("data", raw)

    async def async_get_daily_stats(self, start: date, end: date) -> list[DailyStat]:
        """Bilan par jour sur [start, end] (bornes incluses), toutes sources confondues."""
        if end < start:
            raise ValueError("end doit être ≥ start")

        # Un appel par mois couvert.
        months: list[date] = []
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            months.append(cursor)
            cursor = (cursor + timedelta(days=32)).replace(day=1)

        results: list[DailyStat] = []
        for month in months:
            data = await self.async_get_stats(month, "days")
            results.extend(_parse_month_days(data, month, start, end))
        results.sort(key=lambda d: d.day)
        return results

    async def async_get_export_kwh(self, start: date, end: date) -> float:
        """Énergie injectée sur le réseau (kWh) sur [start, end], bornes incluses.

        Sert à situer la période par rapport au plafond annuel de l'obligation
        d'achat. Les mois entièrement couverts sont lus en une requête ``months``
        (12 mois d'un coup) ; les mois partiels sont détaillés au jour.
        """
        if end < start:
            return 0.0

        total = 0.0
        year_cache: dict[int, dict[int, float]] = {}

        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            next_month = (cursor + timedelta(days=32)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            if cursor >= start and last_day <= end:
                if cursor.year not in year_cache:
                    data = await self.async_get_stats(date(cursor.year, 1, 1), "months")
                    year_cache[cursor.year] = _parse_year_months(data)
                total += year_cache[cursor.year].get(cursor.month, 0.0)
            else:
                days = await self.async_get_daily_stats(
                    max(start, cursor), min(end, last_day)
                )
                total += sum(d.grid_export_kwh for d in days)
            cursor = next_month
        return round(total, 3)


# ---------------------------------------------------------------- parsing


def _last_point(serie: list[dict[str, Any]] | None) -> tuple[float | None, datetime | None]:
    if not serie:
        return None, None
    last = serie[-1]
    try:
        value = float(last.get("y"))
    except (TypeError, ValueError):
        value = None
    ts = last.get("x")
    when = datetime.fromtimestamp(ts / 1000).astimezone() if isinstance(ts, (int, float)) else None
    return value, when


def _clamp_ids(raw: dict[str, Any], kind: str) -> list[str]:
    ids: list[str] = []
    for sources in (raw.get("prod_conso_clamps") or {}).get(kind, {}).values():
        ids.extend(sources.keys())
    return ids


def _parse_dashboard(raw: dict[str, Any]) -> DashboardData:
    lines = {
        f"{line.get('drive_elec_mac')}_{line.get('code')}": line
        for line in raw.get("drive_elec_lines") or []
    }
    prod_ids = _clamp_ids(raw, "prod")
    conso_ids = _clamp_ids(raw, "conso")

    prod_id = prod_ids[0] if prod_ids else None
    conso_id = conso_ids[0] if conso_ids else None

    prod_power, prod_ts = _last_point((lines.get(prod_id) or {}).get("serie"))
    conso_power, conso_ts = _last_point((lines.get(conso_id) or {}).get("serie"))

    network = raw.get("dataNetwork") or {}
    forecast = raw.get("previsionData") or {}
    any_line = next(iter(lines.values()), {})

    def _kwh(key: str) -> float:
        try:
            return float(network.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    production = _kwh("prod")
    consumption = _kwh("conso")
    self_consumption = _kwh("autoconso")

    return DashboardData(
        production_power=prod_power,
        consumption_power=conso_power,
        production_today=production,
        consumption_today=consumption,
        self_consumption_today=self_consumption,
        # FHE ne rafraîchit "reseau"/"surprod" que toutes les quelques heures
        # (figés la nuit puis rattrapés d'un bloc) : on les dérive des trois
        # totaux ci-dessus, eux mis à jour en continu.
        grid_import_today=round(max(consumption - self_consumption, 0.0), 3),
        grid_export_today=round(max(production - self_consumption, 0.0), 3),
        forecast_today=(forecast.get("today") or {}).get("day"),
        forecast_tomorrow=(forecast.get("tomorrow") or {}).get("day"),
        production_clamp_id=prod_id,
        consumption_clamp_id=conso_id,
        device_mac=any_line.get("drive_elec_mac_address"),
        device_label=any_line.get("drive_elec_label"),
        last_sample=max(t for t in (prod_ts, conso_ts) if t) if (prod_ts or conso_ts) else None,
        raw=raw,
    )


def _parse_month_days(
    data: dict[str, Any], month: date, start: date, end: date
) -> list[DailyStat]:
    """Transforme la réponse ``pas=days`` d'un mois en liste de DailyStat."""
    series = (data.get("series") or {}).get("conso_prod_home") or []
    prod_serie = next((s for s in series if s.get("stack") == "prod"), None)
    conso_serie = next((s for s in series if s.get("stack") == "conso"), None)
    rates = data.get("autoconsoprod_data") or {}

    def _by_day(serie: dict[str, Any] | None) -> dict[int, float]:
        out: dict[int, float] = {}
        for point in (serie or {}).get("data") or []:
            try:
                out[int(point["x"])] = float(point.get("y") or 0.0) / 1000.0
            except (KeyError, TypeError, ValueError):
                continue
        return out

    prod_by_day = _by_day(prod_serie)
    conso_by_day = _by_day(conso_serie)

    results: list[DailyStat] = []
    for day_num in sorted(set(prod_by_day) | set(conso_by_day)):
        try:
            day = date(month.year, month.month, day_num)
        except ValueError:
            continue
        if day < start or day > end:
            continue
        prod = prod_by_day.get(day_num, 0.0)
        conso = conso_by_day.get(day_num, 0.0)
        rate = rates.get(str(day_num)) or rates.get(day_num) or {}
        autoconso_pct = _float_or_none(rate.get("autoconso"))
        autoprod_pct = _float_or_none(rate.get("autoprod"))
        # FHE nomme "autoconso" la part de la production consommée sur place,
        # et "autoprod" la part de la consommation couverte par le solaire.
        self_kwh = prod * autoconso_pct / 100.0 if autoconso_pct is not None else min(prod, conso)
        results.append(
            DailyStat(
                day=day,
                production_kwh=round(prod, 3),
                consumption_kwh=round(conso, 3),
                self_consumption_kwh=round(self_kwh, 3),
                grid_import_kwh=round(max(conso - self_kwh, 0.0), 3),
                grid_export_kwh=round(max(prod - self_kwh, 0.0), 3),
                self_consumption_rate=autoconso_pct,
                self_sufficiency_rate=autoprod_pct,
            )
        )
    return results


def _parse_year_months(data: dict[str, Any]) -> dict[int, float]:
    """Injection (kWh) par mois, depuis la réponse ``pas=months`` d'une année."""
    series = (data.get("series") or {}).get("conso_prod_home") or []
    prod_serie = next((s for s in series if s.get("stack") == "prod"), None)
    rates = data.get("autoconsoprod_data") or {}

    out: dict[int, float] = {}
    for point in (prod_serie or {}).get("data") or []:
        try:
            month = int(point["x"])
            prod = float(point.get("y") or 0.0) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        rate = rates.get(str(month)) or rates.get(month) or {}
        autoconso_pct = _float_or_none(rate.get("autoconso"))
        # Sans taux connu, on ne peut pas distinguer autoconso et injection :
        # on suppose tout autoconsommé (hypothèse basse sur l'injection).
        exported = prod * (1 - autoconso_pct / 100.0) if autoconso_pct is not None else 0.0
        out[month] = round(max(exported, 0.0), 3)
    return out


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
