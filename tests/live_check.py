"""Vérification en direct contre le portail FHE.

Usage (les identifiants ne sont jamais écrits sur disque) :

    FHE_USERNAME=xxx FHE_PASSWORD=yyy .venv/bin/python tests/live_check.py
"""

import asyncio
from datetime import date, timedelta
import importlib.util
import os
from pathlib import Path
import sys

import aiohttp

_spec = importlib.util.spec_from_file_location(
    "fhe_api", Path(__file__).resolve().parents[1] / "custom_components" / "fhe_solar" / "api.py"
)
_api = importlib.util.module_from_spec(_spec)
sys.modules["fhe_api"] = _api
_spec.loader.exec_module(_api)


async def main() -> None:
    username = os.environ.get("FHE_USERNAME")
    password = os.environ.get("FHE_PASSWORD")
    if not username or not password:
        sys.exit("Définir FHE_USERNAME et FHE_PASSWORD dans l'environnement")

    async with aiohttp.ClientSession() as session:
        client = _api.FheClient(session, username, password)
        await client.login()
        print("Login OK")

        d = await client.async_get_dashboard()
        print(f"Appareil        : {d.device_label} ({d.device_mac})")
        print(f"Puissance prod  : {d.production_power} W   (échantillon {d.last_sample})")
        print(f"Puissance conso : {d.consumption_power} W")
        print(f"Aujourd'hui     : prod {d.production_today} kWh | conso {d.consumption_today} kWh | "
              f"autoconso {d.self_consumption_today} kWh | réseau {d.grid_import_today} | "
              f"injection {d.grid_export_today}")
        print(f"Taux            : autoconso {d.self_consumption_rate} % | autoprod {d.self_sufficiency_rate} %")

        today = date.today()
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        days = await client.async_get_daily_stats(start, end)
        print(f"\nSemaine {start} → {end} :")
        for day in days:
            print(f"  {day.day}  prod {day.production_kwh:6.2f}  conso {day.consumption_kwh:6.2f}  "
                  f"auto {day.self_consumption_kwh:6.2f}  import {day.grid_import_kwh:6.2f}  "
                  f"export {day.grid_export_kwh:5.2f}  ({day.self_consumption_rate} % / {day.self_sufficiency_rate} %)")
        print(f"  TOTAL prod {sum(x.production_kwh for x in days):.2f} kWh, conso {sum(x.consumption_kwh for x in days):.2f} kWh")


asyncio.run(main())
