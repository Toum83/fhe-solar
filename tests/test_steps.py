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
detect_steps, parse_serie = _mod.detect_steps, _mod.parse_serie

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
