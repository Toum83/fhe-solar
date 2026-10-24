"""Gaz GRDF au jour réel : calculs purs (sans Home Assistant), testés par tests/test_gas_days.py.

Gazpar ne publie qu'un total par jour, avec 1 à 3 jours de retard et souvent plusieurs jours
d'un coup. Un capteur HA range l'énergie au moment où sa valeur change, donc au jour de
publication. On écrit plutôt une statistique datée : chaque journée dans l'heure 23 h – minuit
de son vrai jour (la barre « jour » du tableau Énergie tombe alors sur le bon jour).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, tzinfo
from typing import Any

#: Heure locale où l'on range le total d'une journée.
DAY_STAMP_HOUR = 23


def parse_daily(entries: Iterable[Mapping[str, Any]] | None) -> dict[date, float]:
    """Lit l'attribut ``daily`` de sensor.gazpar : [{time_period: "JJ/MM/AAAA", energy_kwh}, ...]."""
    out: dict[date, float] = {}
    for item in entries or []:
        try:
            dd, mm, yyyy = str(item["time_period"]).split("/")[:3]
            day = date(int(yyyy), int(mm), int(dd))
            kwh = float(item["energy_kwh"])
        except (KeyError, TypeError, ValueError):
            continue
        if kwh >= 0:
            out[day] = kwh
    return out


def stamp(day: date, tz: tzinfo) -> datetime:
    """Début de l'heure qui porte le total du jour (23 h locale)."""
    return datetime.combine(day, time(DAY_STAMP_HOUR), tzinfo=tz)


def cumulative_rows(days: Mapping[date, float], base_sum: float, tz: tzinfo) -> list[tuple[datetime, float, float]]:
    """(début d'heure, cumul, énergie du jour) pour chaque jour, dans l'ordre, à partir de ``base_sum``.

    ``base_sum`` est le cumul de la statistique juste avant le premier jour : les jours déjà
    écrits et republiés par GRDF (corrections) sont réécrits à l'identique, sans double compte.
    """
    rows: list[tuple[datetime, float, float]] = []
    total = base_sum
    for day in sorted(days):
        total += days[day]
        rows.append((stamp(day, tz), round(total, 3), days[day]))
    return rows
