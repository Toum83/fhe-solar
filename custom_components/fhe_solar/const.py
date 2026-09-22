"""Constants for the FHE Solar integration."""

from __future__ import annotations

DOMAIN = "fhe_solar"

BASE_URL = "https://fhesmart.fhe-france.com"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_PRICE_IMPORT = "price_import"
CONF_PRICE_EXPORT = "price_export"
CONF_PRICE_EXPORT_ABOVE = "price_export_above"
CONF_EXPORT_CAP_KWH = "export_cap_kwh"
CONF_CONTRACT_ANNIVERSARY = "contract_anniversary"

DEFAULT_SCAN_INTERVAL = 5  # minutes
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60

# Tarifs par défaut (contrat de Thomas, 2026) — modifiables dans les options.
# Import : tarif base, 0,2051 €/kWh hors abonnement.
# Export (obligation d'achat) : 0,10 €/kWh jusqu'au plafond annuel de
# 1143 kWh livrés, puis 0,05 €/kWh au-delà.
DEFAULT_PRICE_IMPORT = 0.2051
DEFAULT_PRICE_EXPORT = 0.10
DEFAULT_PRICE_EXPORT_ABOVE = 0.05
DEFAULT_EXPORT_CAP_KWH = 1143.0
# Début de l'année contractuelle pour le plafond, au format MM-DD.
# Contrat d'obligation d'achat : remise à zéro du plafond au 10 mai.
DEFAULT_CONTRACT_ANNIVERSARY = "05-10"

SERVICE_WEEKLY_REPORT = "get_weekly_report"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"
ATTR_PRICE_IMPORT = "price_import"
ATTR_PRICE_EXPORT = "price_export"
ATTR_PRICE_EXPORT_ABOVE = "price_export_above"
ATTR_EXPORT_CAP_KWH = "export_cap_kwh"
