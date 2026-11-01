"""Échelons de puissance détectés sur la courbe à 7 s du tableau de bord FHE.

Chaque réponse de ``/dashboard/data/all_widgets`` contient, par pince, les 500 derniers points
(un toutes les 7 s, soit ~58 min ; constaté le 31/10/2026). L'intégration n'en gardait que le
dernier, une fois par minute : le NILM datait les démarrages à ±1–2 min et collait les chauffes
rapprochées. Ici on parcourt tous les points nouveaux depuis le relevé précédent et on en tire
les échelons (démarrages / arrêts), envoyés ensuite au NILM par un événement HA.

- Un échelon étalé sur plusieurs points de même sens (montée sur 14 s) est un seul échelon.
- Une impulsion d'un seul point qui revient au niveau de départ donne un démarrage et un arrêt.
- Une pointe suivie d'un palier plus bas (appel de courant d'un moteur) : le palier compte.

Module sans dépendance à HA (testé seul).
"""

from __future__ import annotations

from dataclasses import dataclass

# Plus petite variation entre deux points qui prolonge un échelon déjà commencé.
CONTINUE_W = 40
# Seuil bas : l'automatisation HA applique ensuite input_number.nilm_event_threshold_w (80 W depuis
# le 31/10/2026). Le bruit de la courbe à 7 s est d'environ ±40 W : 60 W reste au-dessus.
MIN_STEP_W = 60
# Sous ce seuil, une impulsion d'un point est du bruit, et un échelon doit tenir sur les 2 points
# suivants (à la moitié de sa hauteur près) : le fond de la maison oscille parfois de ±80 W en 7 s.
SMALL_STEP_W = 200
# Pointe qui redescend d'au moins cette part au point suivant : appel de courant.
SPIKE_RETURN = 0.6
# Au-delà, deux points ne sont pas consécutifs (trou dans la courbe FHE) : pas d'échelon à cheval.
MAX_GAP_MS = 60_000


@dataclass
class Step:
    ts_ms: int  # premier point au nouveau niveau
    power_before: float
    power_after: float
    production_power: float | None

    @property
    def delta_w(self) -> float:
        return self.power_after - self.power_before


def parse_serie(serie: list[dict] | None) -> list[tuple[int, float]]:
    """Points ``{x: ms, y: W}`` → liste triée de (ms, W), sans doublons ni valeurs invalides."""
    out: dict[int, float] = {}
    for p in serie or []:
        try:
            out[int(p["x"])] = float(p["y"])
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out.items())


def detect_steps(
    points: list[tuple[int, float]],
    after_ms: int | None,
    production: dict[int, float] | None = None,
) -> list[Step]:
    """Échelons dont le nouveau niveau est atteint après ``after_ms`` (exclu).

    ``points`` doit inclure au moins le point précédant ``after_ms`` pour que le premier
    échelon nouveau ait son niveau de départ.
    """
    production = production or {}
    steps: list[Step] = []
    i = 0
    n = len(points)
    while i < n - 1:
        t0, w0 = points[i]
        t1, w1 = points[i + 1]
        d = w1 - w0
        if t1 - t0 > MAX_GAP_MS or abs(d) < CONTINUE_W:
            i += 1
            continue
        # Impulsion d'un seul point (le point suivant revient au niveau de départ) : un vrai
        # démarrage suivi d'un arrêt. Cas réel du 31/10/2026 : four en régulation, 2,3 kW pendant
        # 7 s toutes les 70 s ; au relevé à la minute ces impulsions tombaient au hasard.
        if i + 2 < n and points[i + 2][0] - t1 <= MAX_GAP_MS:
            t2, w2 = points[i + 2]
            if (w2 - w1) * d < 0 and abs(w2 - w0) < MIN_STEP_W:
                if abs(d) >= SMALL_STEP_W:
                    for ts, a, b in ((t1, w0, w1), (t2, w1, w2)):
                        if after_ms is None or ts > after_ms:
                            steps.append(Step(ts, a, b, production.get(ts)))
                i += 2
                continue
        # Appel de courant (moteur) : pointe puis palier plus bas, le palier est le vrai niveau.
        overshoot = (
            i + 2 < n
            and points[i + 2][0] - t1 <= MAX_GAP_MS
            and (points[i + 2][1] - w1) * d < 0
            and abs(points[i + 2][1] - w1) >= SPIKE_RETURN * abs(d)
        )
        # Échelon étalé : on prolonge tant que les variations gardent le même sens.
        j = i + 1
        while (
            j + 1 < n
            and points[j + 1][0] - points[j][0] <= MAX_GAP_MS
            and (points[j + 1][1] - points[j][1]) * d > 0
            and abs(points[j + 1][1] - points[j][1]) >= CONTINUE_W
        ):
            j += 1
        if overshoot and j == i + 1:
            j = i + 2
        before, after = w0, points[j][1]
        ts = points[i + 1][0]
        height = abs(after - before)
        if MIN_STEP_W <= height < SMALL_STEP_W:
            held = points[j + 1 : j + 3]
            if len(held) < 2 or any(abs(w - after) > height / 2 for _, w in held):
                i = j
                continue
        if height >= MIN_STEP_W and (after_ms is None or ts > after_ms):
            steps.append(Step(ts, before, after, production.get(ts)))
        i = j
    return steps


def new_curve_points(
    conso: list[tuple[int, float]],
    production: dict[int, float],
    after_ms: int | None,
) -> list[list[int | None]]:
    """Points de la courbe postérieurs à ``after_ms``, en ``[ms, conso W, prod W ou None]``.

    Format compact pour l'archive (une ligne par point à 7 s côté Supabase). Sans ``after_ms``
    (premier relevé), toute l'heure que FHE conserve est renvoyée.
    """
    return [
        [ts, round(w), None if production.get(ts) is None else round(production[ts])]
        for ts, w in conso
        if after_ms is None or ts > after_ms
    ]

