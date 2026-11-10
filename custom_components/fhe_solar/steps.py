"""Échelons de puissance détectés sur la courbe à 7 s du tableau de bord FHE.

Chaque réponse de ``/dashboard/data/all_widgets`` contient, par pince, les 500 derniers points
(un toutes les 7 s, soit ~58 min ; constaté le 31/10/2026). L'intégration n'en gardait que le
dernier, une fois par minute : le NILM datait les démarrages à ±1–2 min et collait les chauffes
rapprochées. Ici on parcourt tous les points nouveaux depuis le relevé précédent et on en tire
les échelons (démarrages / arrêts), envoyés ensuite au NILM par un événement HA.

- Un échelon étalé sur plusieurs points de même sens (montée sur 14 s) est un seul échelon.
- Une impulsion d'un seul point qui revient au niveau de départ donne un démarrage et un arrêt.
- Une pointe suivie d'un palier plus bas (appel de courant d'un moteur) : le palier compte.

Depuis la 0.6.0, les échelons sont cherchés sur la puissance de la MAISON, reconstruite par
``house_series`` : la pince « Consommation » du Drive&Elec mesure en fait l'échange avec le réseau
(maison − production). Constaté le 10/11/2026 : quand la production varie, la « consommation »
varie d'autant en sens inverse (pente −0,96 à −0,99), et au soleil elle est inférieure à ce que
mesure une prise connectée sur le lave-vaisselle seul. Chaque éclaircie fabriquait de faux arrêts.

Module sans dépendance à HA (testé seul).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

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


# La pince « Production » lit ~120 W dès que l'onduleur est sous tension, sans puissance réelle :
# 117–123 W pendant une heure au crépuscule, puis 0 (seuil d'affichage FHE à 100 W). On retire ce
# plancher en quadrature (courant réactif), ce qui raccorde sans saut au lever et au coucher.
PROD_FLOOR_W = 120.0
# Quand la maison injecte (production > consommation), FHE répète la dernière valeur de conso au
# watt près : jusqu'à 361 points de suite le 06/11/2026, jamais plus de 9 sans production. À partir
# de 4 valeurs identiques sous production, la maison est tenue pour inconnue : niveau maintenu.
FREEZE_MIN_POINTS = 4
# Points de fin de courbe laissés au relevé suivant : 2 pour juger une pointe ou un échelon étalé,
# plus de quoi savoir si une suite de valeurs identiques est un gel.
PENDING_POINTS = 2 + FREEZE_MIN_POINTS


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


def effective_production(watts: float) -> float:
    """Production réelle estimée à partir de la lecture de la pince (plancher réactif retiré)."""
    return math.sqrt(max(watts * watts - PROD_FLOOR_W * PROD_FLOOR_W, 0.0))


def house_series(
    conso: list[tuple[int, float]], production: dict[int, float] | None
) -> list[tuple[int, float]]:
    """Puissance de la maison : échange réseau + production, niveau maintenu pendant l'injection.

    ``conso`` est la courbe de la pince « Consommation » (en réalité le soutirage). Tant que la
    maison soutire, maison = conso + production. Pendant une injection, FHE fige la conso : la
    maison vaut alors moins que la production, sans qu'on sache combien. On garde le niveau du
    premier point de la suite figée, ce qui n'invente aucun échelon ; l'écart réel apparaît en un
    seul échelon à la reprise du soutirage.
    """
    production = production or {}
    out: list[tuple[int, float]] = []
    last_prod = 0.0
    i, n = 0, len(conso)
    while i < n:
        j = i
        while j + 1 < n and conso[j + 1][1] == conso[i][1] and conso[j + 1][0] - conso[j][0] <= MAX_GAP_MS:
            j += 1
        prods = []
        for k in range(i, j + 1):
            last_prod = production.get(conso[k][0], last_prod)
            prods.append(effective_production(last_prod))
        if j - i + 1 >= FREEZE_MIN_POINTS and min(prods) > 0:
            held = conso[i][1] + prods[0]
            out.extend((conso[k][0], held) for k in range(i, j + 1))
        else:
            out.extend((conso[k][0], conso[k][1] + p) for k, p in zip(range(i, j + 1), prods))
        i = j + 1
    return out


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

