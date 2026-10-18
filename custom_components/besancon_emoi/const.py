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
# Appariement contrat d'eau → entité source d'index (comptes à plusieurs
# contrats : on n'envoie jamais le même index à plusieurs compteurs).
CONF_INDEX_ENTITIES = "index_entities"

# Calendrier de collecte des déchets (open data Grand Besançon).
CONF_COMMUNE = "collecte_commune"
CONF_RUE = "collecte_rue"
CONF_NUMERO = "collecte_numero"

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
