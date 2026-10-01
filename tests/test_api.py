"""Tests du parsing FHE sur des réponses réelles (séries tronquées)."""

from datetime import date
import json
from pathlib import Path
import sys

import importlib.util

# api.py est chargé directement pour ne pas importer le package (qui dépend de HA).
_spec = importlib.util.spec_from_file_location(
    "fhe_api", Path(__file__).resolve().parents[1] / "custom_components" / "fhe_solar" / "api.py"
)
_api = importlib.util.module_from_spec(_spec)
sys.modules["fhe_api"] = _api
_spec.loader.exec_module(_api)
_parse_dashboard = _api._parse_dashboard
_parse_month_days = _api._parse_month_days
_parse_year_months = _api._parse_year_months

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_dashboard():
    d = _parse_dashboard(json.loads((FIXTURES / "dashboard.json").read_text()))
    assert d.production_clamp_id == "0004A307CC99_1"
    assert d.consumption_clamp_id == "0004A307CC99_2"
    assert d.production_power == 0
    assert d.consumption_power == 468
    assert d.production_today == 5.8
    assert d.consumption_today == 12.76
    assert d.self_consumption_today == 5.22
    assert d.grid_import_today == 7.54
    assert d.grid_export_today == 0.58
    assert d.self_consumption_rate == 90.0
    assert d.self_sufficiency_rate == 40.9
    assert d.device_mac == "0004A307CC99"
    assert d.last_sample is not None


def test_parse_month_days_week():
    data = json.loads((FIXTURES / "stats_days.json").read_text())["data"]
    days = _parse_month_days(data, date(2026, 9, 1), date(2026, 9, 14), date(2026, 9, 20))
    assert [d.day.day for d in days] == [14, 15, 16, 17, 18, 19, 20]
    sunday = days[-1]
    assert sunday.production_kwh == 5.777
    assert sunday.consumption_kwh == 12.749
    assert sunday.self_consumption_rate == 89.93
    # 5.777 × 89.93 % ≈ 5.195 kWh autoconsommés, cohérent avec le 5.22 du dashboard
    assert abs(sunday.self_consumption_kwh - 5.195) < 0.01
    assert abs(sunday.grid_export_kwh - 0.58) < 0.01
    assert sum(d.production_kwh for d in days) > 50


def test_parse_month_days_missing_rate():
    data = json.loads((FIXTURES / "stats_days.json").read_text())["data"]
    # 7 et 8 septembre : pas de données ni de taux → 0 partout, pas d'exception
    days = _parse_month_days(data, date(2026, 9, 1), date(2026, 9, 7), date(2026, 9, 8))
    assert len(days) == 2
    assert all(d.production_kwh == 0 and d.self_consumption_kwh == 0 for d in days)


def test_parse_year_months():
    """Injection mensuelle = production × (1 - taux d'autoconsommation).

    Le fichier reprend la forme réelle de la réponse `pas=months` ; janvier et
    septembre portent les vraies valeurs relevées sur le portail.
    """
    data = json.loads((FIXTURES / "stats_months.json").read_text())["data"]
    months = _parse_year_months(data)
    # Janvier : 34,965 kWh produits, 91,93 % autoconsommés → ~2,82 kWh injectés
    assert abs(months[1] - 2.822) < 0.01
    # Septembre : 143,68 kWh produits, 62,49 % autoconsommés → ~53,9 kWh injectés
    assert abs(months[9] - 53.89) < 0.05
    # Mars n'a pas de taux connu : on n'invente pas d'injection
    assert months[3] == 0.0
    assert months[12] == 0.0


def test_tier_split():
    """Découpage d'une injection hebdomadaire sur le barème à paliers."""

    def split(exported, before, cap=1143.0):
        remaining = max(cap - before, 0.0)
        tier1 = min(exported, remaining)
        return round(tier1, 2), round(exported - tier1, 2)

    assert split(20.0, 0.0) == (20.0, 0.0)          # loin du plafond
    assert split(20.0, 1135.0) == (8.0, 12.0)       # à cheval sur le plafond
    assert split(20.0, 1200.0) == (0.0, 20.0)       # plafond déjà dépassé


if __name__ == "__main__":
    test_parse_dashboard()
    test_parse_month_days_week()
    test_parse_month_days_missing_rate()
    test_parse_year_months()
    test_tier_split()
    print("OK")


def test_overlay_energy_statistics():
    from datetime import date as d

    DailyStat = _api.DailyStat
    overlay = _api.overlay_energy_statistics

    def fhe(day, prod):
        return DailyStat(day, prod, 10.0, 2.0, 8.0, prod - 2.0, 50.0, 20.0)

    days = [fhe(d(2026, 9, 25), 8.4), fhe(d(2026, 9, 26), 2.569), fhe(d(2026, 9, 27), 7.3), fhe(d(2026, 9, 28), 5.0)]
    prod = {d(2026, 9, 25): 8.68, d(2026, 9, 26): 5.43, d(2026, 9, 28): 5.9}
    imp = {d(2026, 9, 25): 9.0, d(2026, 9, 26): 8.0, d(2026, 9, 27): 11.9, d(2026, 9, 28): 7.0}
    exp = {d(2026, 9, 25): 2.2, d(2026, 9, 26): 0.9, d(2026, 9, 28): 1.0}
    out = overlay(days, prod, imp, exp, today=d(2026, 9, 28))

    # 26 sept. : remplacé par les stats HA, valeurs dérivées cohérentes
    s26 = out[1]
    assert s26.source == "ha_statistics"
    assert s26.production_kwh == 5.43
    assert s26.self_consumption_kwh == 4.53  # prod - injection
    assert s26.consumption_kwh == 12.53  # autoconso + soutirage
    assert s26.grid_import_kwh == 8.0 and s26.grid_export_kwh == 0.9
    assert s26.self_consumption_rate == round(100 * 4.53 / 5.43, 2)
    # 27 sept. : pas de production HA → on garde FHE
    assert out[2].source == "fhe" and out[2].production_kwh == 7.3
    # jour courant : jamais remplacé (statistiques incomplètes)
    assert out[3].source == "fhe" and out[3].production_kwh == 5.0


def test_overlay_rejects_implausible_ha_values():
    from datetime import date as d

    s = _api.DailyStat(d(2026, 9, 20), 8.0, 12.0, 4.0, 8.0, 4.0, 50.0, 33.0)
    # injection > production : incohérent, on garde FHE
    out = _api.overlay_energy_statistics([s], {d(2026, 9, 20): 3.0}, {d(2026, 9, 20): 5.0}, {d(2026, 9, 20): 6.0}, d(2026, 10, 1))
    assert out[0].source == "fhe"
    # statistique négative : idem
    out = _api.overlay_energy_statistics([s], {d(2026, 9, 20): 3.0}, {d(2026, 9, 20): -1.0}, {d(2026, 9, 20): 1.0}, d(2026, 10, 1))
    assert out[0].source == "fhe"
