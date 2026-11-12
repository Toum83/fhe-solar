"""Tests de la détection d'échelons sur la courbe FHE à 7 s."""

import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "steps", Path(__file__).parent.parent / "custom_components" / "fhe_solar" / "steps.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["steps"] = _mod  # requis par @dataclass
_spec.loader.exec_module(_mod)
detect_steps, parse_serie, new_curve_points = _mod.detect_steps, _mod.parse_serie, _mod.new_curve_points
house_series, effective_production = _mod.house_series, _mod.effective_production
real_consumption, curve_energy = _mod.real_consumption, _mod.curve_energy

T0 = 1_793_460_000_000


def curve(*levels):
    """Un point toutes les 7 s."""
    return [(T0 + i * 7000, float(w)) for i, w in enumerate(levels)]


def test_echelon_franc_puis_arret():
    pts = curve(300, 305, 2300, 2310, 2295, 310, 300)
    s = detect_steps(pts, None)
    assert [(x.ts_ms, x.power_before, x.power_after) for x in s] == [
        (T0 + 2 * 7000, 305, 2300),
        (T0 + 5 * 7000, 2295, 310),
    ]


def test_echelon_etale_sur_deux_points_compte_une_fois():
    s = detect_steps(curve(300, 1100, 2300, 2300), None)
    assert len(s) == 1 and s[0].power_before == 300 and s[0].power_after == 2300


def test_impulsion_d_un_point_donne_demarrage_et_arret():
    # Four en régulation (31/10/2026) : 2,3 kW pendant 7 s.
    s = detect_steps(curve(1240, 1240, 3503, 1223, 1232), None)
    assert [(x.ts_ms, x.delta_w > 0) for x in s] == [(T0 + 2 * 7000, True), (T0 + 3 * 7000, False)]


def test_impulsion_de_deux_points():
    s = detect_steps(curve(1263, 1270, 3526, 3545, 1263, 1240), None)
    assert [(x.ts_ms, round(x.delta_w)) for x in s] == [(T0 + 2 * 7000, 2256), (T0 + 4 * 7000, -2282)]


def test_appel_de_courant_moteur_garde_le_palier():
    s = detect_steps(curve(300, 2000, 800, 790, 800), None)
    assert len(s) == 1 and s[0].power_after == 800


def test_petites_variations_ignorees():
    assert detect_steps(curve(1064, 1142, 1072, 1100, 1060), None) == []


def test_seuls_les_echelons_apres_le_dernier_point_traite():
    pts = curve(300, 2300, 2300, 300, 300)
    s = detect_steps(pts, T0 + 2 * 7000)
    assert len(s) == 1 and s[0].delta_w == -2000


def test_trou_dans_la_courbe_pas_d_echelon_a_cheval():
    pts = [(T0, 300.0), (T0 + 120_000, 2300.0), (T0 + 127_000, 2300.0)]
    assert detect_steps(pts, None) == []


def test_production_au_meme_instant():
    pts = curve(300, 2300, 2300)
    s = detect_steps(pts, None, {T0 + 7000: 850.0})
    assert s[0].production_power == 850.0


def test_parse_serie_trie_et_ignore_les_points_invalides():
    assert parse_serie([{"x": 2, "y": 5}, {"x": 1, "y": "4"}, {"x": 3}, None and {}]) == [(1, 4.0), (2, 5.0)]


def test_demarrage_d_un_pc_au_dessus_du_bruit():
    # 31/10/2026 20 h 42 : PC allumé, +115 W ; le bruit de ±40 W ne donne rien.
    s = detect_steps(curve(326, 340, 318, 441, 430, 452, 421), None)
    assert [(x.power_before, x.power_after) for x in s] == [(318, 441)]


def test_petite_impulsion_de_bruit_ignoree():
    # Fond à 1,2 kW qui oscille (31/10/2026 15 h 55) : +152 W sur un seul point.
    assert detect_steps(curve(1215, 1367, 1219, 1161, 1163), None) == []


def test_petit_echelon_qui_ne_tient_pas_ignore():
    assert detect_steps(curve(300, 300, 420, 330, 310, 300), None) == []


def test_curve_points_after_last_sent():
    conso = curve(300, 310.4, 2290)
    prod = {T0: 1200.6, T0 + 7000: 1180}
    assert new_curve_points(conso, prod, T0) == [[T0 + 7000, 310, 1180], [T0 + 14000, 2290, None]]


def test_curve_points_first_run_sends_whole_hour():
    assert len(new_curve_points(curve(*[300] * 500), {}, None)) == 500


def prod_of(*levels):
    return {T0 + i * 7000: float(w) for i, w in enumerate(levels)}


def test_maison_eclaircie_sous_une_chauffe_sans_echelon():
    # 05/11/2026 : chauffe-eau en marche, la production saute de 240 à 1 150 W, la conso FHE
    # (soutirage) plonge d'autant. Sur la conso seule : un faux arrêt puis un faux démarrage.
    conso = curve(1990, 1985, 1080, 1075, 1070, 1985, 1990, 1988)
    prod = prod_of(240, 245, 1150, 1155, 1160, 245, 240, 242)
    assert len(detect_steps(conso, None, prod)) == 2
    assert detect_steps(house_series(conso, prod), None, prod) == []


def test_maison_echelon_a_sa_vraie_taille_au_soleil():
    # Chauffe du lave-vaisselle (2 100 W à la prise) démarrée pendant une injection : la conso FHE
    # passait de 279 W figés à 1 850 W, soit un échelon raboté à ~1 570 W.
    conso = curve(279, 279, 279, 279, 279, 1850, 1848, 1852, 1850)
    prod = prod_of(440, 445, 450, 448, 452, 450, 449, 451, 450)
    s = detect_steps(house_series(conso, prod), None, prod)
    assert len(s) == 1 and s[0].delta_w > 1550
    assert s[0].power_before == real_consumption(279) + effective_production(440)


def test_maison_gel_pendant_l_injection_niveau_maintenu():
    conso = curve(300, 290, 279, 279, 279, 279, 279, 279, 285, 295)
    prod = prod_of(250, 260, 300, 420, 535, 610, 480, 330, 300, 290)
    h = house_series(conso, prod)
    held = real_consumption(279) + effective_production(300)
    assert [w for _, w in h[2:8]] == [held] * 6
    assert detect_steps(h, None, prod) == []


def test_maison_sans_production_conso_reelle():
    conso = curve(250, 250, 250, 250, 250, 2250, 2250, 250)
    expected = [(t, real_consumption(w)) for t, w in conso]
    assert house_series(conso, {}) == expected
    assert house_series(conso, prod_of(0, 0, 0, 0, 0, 0, 0, 0)) == expected


def test_conso_reelle_part_reactive_retiree():
    # Linky 10/11/2026 : ~120 W réels la nuit quand la pince lit ~240 W ; à forte charge, identique.
    assert 115 < real_consumption(240) < 125
    assert abs(real_consumption(2000) - 2000) < 12
    assert real_consumption(150) == 0


def test_energie_soutirage_et_injection_sur_la_courbe():
    # 10 points à 7 s : 3 de soutirage (2 000 W lus), 4 figés sous 1 500 W de production, 3 de soutirage.
    conso = curve(2000, 2000, 2000, 279, 279, 279, 279, 2000, 2000, 2000)
    prod = prod_of(0, 0, 0, 1500, 1500, 1500, 1500, 0, 0, 0)
    t_end = conso[-1][0]
    grid_in, grid_out, house = curve_energy(conso, prod, None, t_end, 0.0)
    # Soutirage : 6 intervalles de 7 s à ~1 989 W réels (le dernier point n'a pas de suivant).
    assert abs(grid_in - 5 * 7 / 3600 * real_consumption(2000) / 1000) < 1e-9
    # Injection : 4 intervalles à 1 500 W de production moins ~1 989 W de maison → 0 (la maison
    # consommait plus que la production : estimation basse assumée).
    assert grid_out == 0
    # Avec une maison à 300 W avant le gel, l'injection vaut production − 300 W.
    conso2 = curve(510, 510, 510, 279, 279, 279, 279, 510)
    g_in, g_out, _ = curve_energy(conso2, prod, None, conso2[-1][0], 0.0)
    assert abs(g_out - 4 * 7 / 3600 * (effective_production(1500) - real_consumption(510)) / 1000) < 1e-9
    # Rien n'est compté deux fois : en deux passes, même total qu'en une.
    a = curve_energy(conso, prod, None, conso[4][0], 0.0)
    b = curve_energy(conso, prod, conso[4][0], t_end, a[2])
    assert abs(a[0] + b[0] - grid_in) < 1e-12 and abs(a[1] + b[1] - grid_out) < 1e-12


def test_maison_plancher_de_production_au_crepuscule():
    # La pince production lit ~120 W puis 0 d'un coup (seuil FHE) : pas d'échelon dans la maison.
    conso = curve(400, 402, 398, 401, 399, 400, 403, 397)
    prod = prod_of(121, 120, 119, 118, 0, 0, 0, 0)
    h = house_series(conso, prod)
    assert max(w for _, w in h) - min(w for _, w in h) < 30
    assert detect_steps(h, None, prod) == []
