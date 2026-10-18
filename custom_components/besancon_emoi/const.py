"""Constantes de l'intégration Besançon et moi."""

from __future__ import annotations

import logging

DOMAIN = "besancon_emoi"
MANUFACTURER = "Ville de Besançon / Grand Besançon Métropole"
BASE_URL = "https://www.besancon-emoi.fr"

LOGGER = logging.getLogger(__name__)

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Saisie hebdomadaire automatique de l'index.
CONF_AUTO_SUBMIT = "auto_submit"
CONF_SUBMIT_WEEKDAY = "submit_weekday"
CONF_INDEX_ENTITY = "index_entity"

DEFAULT_UPDATE_INTERVAL_MIN = 360  # 6 h
WEEKDAYS = [
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
]
