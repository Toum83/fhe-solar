"""Tests de la compensation de l'heure de retard FHE."""

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "hour_lag", Path(__file__).parent.parent / "custom_components" / "fhe_solar" / "hour_lag.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["hour_lag"] = _mod  # requis par @dataclass
_spec.loader.exec_module(_mod)
HourLagCompensator = _mod.HourLagCompensator

T = lambda h, m=0: datetime(2026, 10, 15, h, m)  # noqa: E731


def _run(c, start, minutes, published, cons_w=2000.0, prod_w=500.0):
    out = None
    for i in range(minutes + 1):
        out = c.update(start + timedelta(minutes=i), *published, cons_w, prod_w)
    return out


def test_heure_en_cours_ajoutee_puis_remplacee_par_le_total_fhe():
    c = HourLagCompensator()
    # FHE a publié jusqu'à 11 h (3,09 kWh conso) ; 11 h 05 → 11 h 35 à 2 kW conso, 0,5 kW prod.
    out = _run(c, T(11, 5), 30, (1.0, 3.09, 0.8))
    # 11 h → 11 h 35 (les 5 premières minutes estimées au démarrage) : +1,167 kWh conso.
    assert out["consumption_today"] == round(3.09 + 2 * 35 / 60, 3)
    assert out["production_today"] == round(1.0 + 0.5 * 35 / 60, 3)
    # Relevés continus jusqu'à 12 h 03, puis 12 h 04 : FHE publie 11 h–12 h ; on repart de 12 h
    # (les 4 minutes depuis 12 h restent comptées en plus du total publié).
    _run(c, T(11, 35), 28, (1.0, 3.09, 0.8))
    out = c.update(T(12, 4), 1.5, 5.09, 1.3, 2000.0, 500.0)
    assert out["consumption_today"] == round(5.09 + 2000 * 4 / 60 / 1000, 3)


def test_trou_de_releves_non_extrapole():
    c = HourLagCompensator()
    _run(c, T(11, 5), 5, (0.0, 1.0, 0.0), 3000.0, 0.0)  # 11 h → 11 h 10 : 0,5 kWh
    out = c.update(T(11, 40), 0.0, 1.0, 0.0, 3000.0, 0.0)  # 30 min sans relevé : pas extrapolé
    assert out["consumption_today"] == 1.5


def test_redemarrage_en_cours_d_heure_sans_baisse():
    # HA redémarre à 11 h 40 : les 40 min depuis 11 h sont estimées à la puissance actuelle.
    c = HourLagCompensator()
    out = c.update(T(11, 40), 0.0, 3.0, 0.0, 1500.0, 0.0)
    assert out["consumption_today"] == 4.0


def test_nouveau_jour_repart_de_minuit():
    c = HourLagCompensator()
    c.update(T(23, 50), 10.0, 20.0, 8.0, 1000.0, 0.0)
    out = _run(c, datetime(2026, 10, 16, 0, 1), 30, (0.0, 0.0, 0.0), 1000.0, 0.0)
    assert out["consumption_today"] == round(31 / 60, 3)  # minuit → 0 h 31


def test_demarrage_juste_apres_l_heure_pleine():
    # Démarrage à 12 h 02 : FHE n'a sans doute pas encore publié 11 h–12 h.
    c = HourLagCompensator()
    out = c.update(T(12, 2), 0.0, 5.0, 0.0, 1200.0, 0.0)
    assert out["consumption_today"] == round(5.0 + 1.2 * 62 / 60, 3)  # depuis 11 h


def test_redemarrage_ne_fait_pas_baisser_le_total_du_jour():
    from datetime import date
    today = date(2026, 10, 24)
    floor = _mod.restart_floor
    assert floor(3.459, 3.833, today, today) == 3.833  # après redémarrage : on garde 3,833
    assert floor(3.969, 3.833, today, today) == 3.969  # FHE a rattrapé : la vraie valeur reprend
    assert floor(0.1, 3.833, date(2026, 10, 23), today) == 0.1  # valeur de la veille : pas de plancher
    assert floor(None, 3.833, today, today) is None
