"""Résumé sûr des valeurs écrites dans le journal (SOCLE §8).

Les traces de debug d'entrée et de sortie montrent la forme des données,
jamais un objet entier : une page du portail, un historique ou un ``hass`` n'ont
pas leur place dans le journal, et un dictionnaire de saisie peut
contenir une valeur qu'on ne veut pas y voir. Seuls les scalaires courts
sont affichés tels quels.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum

LONGUEUR_MAX = 60


def resumer(valeur: object) -> str:
    """Représentation courte et sûre d'une valeur pour le journal.

    Args:
        valeur: N'importe quelle valeur.

    Returns:
        str: ``repr`` des scalaires (chaînes tronquées), taille des
        collections, clés des dictionnaires (jamais leurs valeurs), nom du
        type pour le reste.
    """
    if valeur is None or isinstance(valeur, (bool, int, float, Decimal, date, datetime, time, timedelta)):
        return repr(valeur) if not isinstance(valeur, (date, time)) else valeur.isoformat()
    if isinstance(valeur, Enum):
        return f"{type(valeur).__name__}.{valeur.name}"
    if isinstance(valeur, str):
        if len(valeur) <= LONGUEUR_MAX:
            return repr(valeur)
        return repr(valeur[:LONGUEUR_MAX]) + f"…({len(valeur)} car.)"
    if isinstance(valeur, (bytes, bytearray)):
        return f"{type(valeur).__name__}({len(valeur)} octets)"
    if isinstance(valeur, Mapping):
        cles = [str(c) for c in list(valeur)[:8]]
        suite = ", …" if len(valeur) > len(cles) else ""
        return f"{type(valeur).__name__}({len(valeur)} clés : {', '.join(cles)}{suite})"
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return f"{type(valeur).__name__}({len(valeur)})"
    entite = getattr(valeur, "entity_id", None)
    if isinstance(entite, str):
        # État Home Assistant ou entité : l'identifiant, et la valeur d'état
        # (relevé de compteur, prix) qui est justement ce qu'on diagnostique.
        etat = getattr(valeur, "state", None)
        if isinstance(etat, str):
            return f"<{type(valeur).__name__} {entite}={resumer(etat)}>"
        return f"<{type(valeur).__name__} {entite}>"
    return f"<{type(valeur).__name__}>"
