"""Compense l'heure de retard des totaux « du jour » du tableau de bord FHE.

FHE ne publie ses totaux kWh du jour qu'une fois par heure, vers hh:04, pour l'heure écoulée
(constaté le 15/10/2026 : 3,09 → 4,13 kWh à 12:03:58 pour 11 h–12 h). Dans le tableau Énergie
de HA toutes les barres arrivaient donc une heure trop tard, et la dernière heure de la journée
(23 h–minuit) n'était jamais comptée, FHE remettant ses totaux à zéro avant de la publier.

On ajoute au dernier total publié l'énergie de l'heure en cours, intégrée depuis les puissances
instantanées (consommation et production, relevées chaque minute). Quand FHE publie l'heure, le
total publié la contient et l'intégration repart de zéro. Module sans dépendance à HA (testé seul).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# Au-delà, un trou entre deux relevés n'est pas extrapolé (HA arrêté, FHE indisponible…).
MAX_GAP = timedelta(minutes=10)
# FHE publie l'heure écoulée quelques minutes après l'heure pleine.
PUBLISH_DELAY = timedelta(minutes=5)


def _floor_hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


@dataclass
class HourLagCompensator:
    """Totaux du jour FHE + énergie de l'heure en cours estimée depuis les puissances."""

    _published: tuple[float, ...] | None = None
    _covered_until: datetime | None = None
    _day: object | None = None
    _samples: list[tuple[datetime, float, float]] = field(default_factory=list)

    def update(
        self,
        now: datetime,
        production: float,
        consumption: float,
        self_consumption: float,
        consumption_w: float | None,
        production_w: float | None,
    ) -> dict[str, float]:
        """Renvoie les totaux du jour compensés (kWh), arrondis au Wh."""
        published = (production, consumption, self_consumption)
        if self._day != now.date():
            # Nouveau jour : FHE repart de zéro et couvre minuit.
            self._day = now.date()
            self._published = published
            self._covered_until = _floor_hour(now) if now.hour else now.replace(hour=0, minute=0, second=0, microsecond=0)
            if now.hour and now - _floor_hour(now) < PUBLISH_DELAY:
                self._covered_until -= timedelta(hours=1)
            self._samples = []
        elif published != self._published:
            # FHE vient de publier l'heure écoulée : elle est dans le total, on repart de là.
            self._published = published
            self._covered_until = _floor_hour(now)
        if consumption_w is not None and production_w is not None:
            if not self._samples and now > self._covered_until:
                # Démarrage (ou redémarrage de HA) en cours d'heure : l'énergie déjà consommée
                # depuis le dernier total publié est estimée à la puissance actuelle. Sans ça le
                # compteur baisserait au redémarrage, ce que HA prendrait pour une remise à zéro.
                self._samples.append((self._covered_until, max(consumption_w, 0.0), max(production_w, 0.0)))
            self._samples.append((now, max(consumption_w, 0.0), max(production_w, 0.0)))
        self._samples = [s for s in self._samples if s[0] >= self._covered_until - MAX_GAP]

        prod = cons = auto = 0.0  # Wh
        for (t0, c0, p0), (t1, _c1, _p1) in zip(self._samples, self._samples[1:]):
            start = max(t0, self._covered_until)
            if t1 <= start or (t1 - t0 > MAX_GAP and t0 != self._covered_until):
                continue
            hours = (t1 - start).total_seconds() / 3600
            prod += p0 * hours
            cons += c0 * hours
            auto += min(c0, p0) * hours

        production_c = production + prod / 1000
        consumption_c = consumption + cons / 1000
        self_c = self_consumption + auto / 1000
        return {
            "production_today": round(production_c, 3),
            "consumption_today": round(consumption_c, 3),
            "self_consumption_today": round(self_c, 3),
            "grid_import_today": round(max(consumption_c - self_c, 0.0), 3),
            "grid_export_today": round(max(production_c - self_c, 0.0), 3),
        }


def restart_floor(value: float | None, floor: float | None, floor_day: date | None, today: date) -> float | None:
    """Valeur « du jour » protégée contre la baisse qui suit un redémarrage de HA.

    Au redémarrage, la compensation perd l'énergie intégrée depuis la dernière heure publiée
    par FHE : le total redescend jusqu'à la publication suivante (cas réel du 24/10/2026 :
    3,833 → 3,459 kWh à 10 h 47). HA prend une baisse de plus de 10 % pour une remise à zéro et
    ajoute tout le compteur à l'heure en cours (+3,08 kWh de soutirage fantôme). On garde donc la
    dernière valeur connue du même jour tant que le nouveau total ne l'a pas rattrapée.
    """
    if value is None or floor is None or floor_day != today:
        return value
    return max(value, floor)
