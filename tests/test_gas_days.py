"""Tests du gaz au jour réel (calculs purs)."""

from datetime import date, timezone, timedelta
import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "gas_days", Path(__file__).parent.parent / "custom_components" / "fhe_solar" / "gas_days.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["gas_days"] = _mod
_spec.loader.exec_module(_mod)

PARIS = timezone(timedelta(hours=2))


def test_lit_l_attribut_daily_de_gazpar():
    daily = [
        {"time_period": "21/10/2026", "energy_kwh": 27.7, "temperature_degC": 9.57},
        {"time_period": "20/10/2026", "energy_kwh": 30.39},
        {"time_period": "bad", "energy_kwh": 1},
        {"time_period": "19/10/2026"},
    ]
    assert _mod.parse_daily(daily) == {date(2026, 10, 21): 27.7, date(2026, 10, 20): 30.39}
    assert _mod.parse_daily(None) == {}


def test_chaque_jour_a_23h_de_son_vrai_jour_avec_un_cumul_continu():
    rows = _mod.cumulative_rows({date(2026, 10, 20): 30.39, date(2026, 10, 18): 30.84, date(2026, 10, 19): 19.4}, 100.0, PARIS)
    assert [(r[0].day, r[0].hour) for r in rows] == [(18, 23), (19, 23), (20, 23)]
    assert [r[1] for r in rows] == [130.84, 150.24, 180.63]
    assert [r[2] for r in rows] == [30.84, 19.4, 30.39]


def test_republication_reecrit_les_memes_cumuls():
    days = {date(2026, 10, 18): 30.84, date(2026, 10, 19): 19.4}
    assert _mod.cumulative_rows(days, 100.0, PARIS) == _mod.cumulative_rows(dict(days), 100.0, PARIS)
