"""Lecture du total TTC dans le PDF d'une facture d'eau.

La liste des factures du portail ne publie que le montant prélevé
(mensualité ou ajustement annuel), pas le total de la facture. Ce module
extrait ce total du PDF de facture, déjà téléchargé par
`BesanconEmoiClient.async_download_bill_pdf`.

Le PDF est un document variable : l'extraction est volontairement
défensive — une facture sans total lisible ne fait pas échouer la mise à
jour, le capteur reste « inconnu » et un avertissement est journalisé.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

# Étiquettes recherchées, par ordre de priorité : « TOTAL TTC » est
# l'intitulé attendu sur les factures du portail, les autres sont des
# replis observés sur des factures françaises d'eau. Les lignes
# « sous-total » sont ignorées (en-têtes de tableau récapitulatif).
_ETIQUETTES = [
    r"TOTAL\s+TTC",
    r"TOTAL\s+[ÀA]\s+PAYER",
    r"NET\s+[ÀA]\s+PAYER",
    r"MONTANT\s+TOTAL",
]
_LIGNE_SOUS_TOTAL = re.compile(r"SOUS[\s-]?TOTAL", re.IGNORECASE)

# Montant français : « 1 234,56 € », « 1.234,56 », « 45,60 » ; les
# séparateurs de milliers (espace, insécable, insécable fine, point)
# sont retirés avant la conversion. Les centimes sont exigés : un
# « TVA 10 % » ne doit jamais être pris pour un total.
_MONTANT_RE = re.compile(r"\d[\d\u00a0\u202f .]*,\d{2}")


def texte_du_pdf(pdf: bytes) -> str:
    """Extrait le texte d'un PDF, page par page.

    `ValueError` si le document n'est pas un PDF lisible (le coordinateur
    journalise et poursuit la mise à jour).
    """
    if not isinstance(pdf, (bytes, bytearray)):
        raise ValueError("Contenu PDF invalide (pas des octets)")
    try:
        reader = PdfReader(io.BytesIO(bytes(pdf)))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as err:  # pypdf lève des erreurs variées sur un PDF abîmé
        raise ValueError(f"PDF illisible : {err}") from err


def extraire_total_ttc(texte: str) -> float | None:
    """Cherche le total TTC dans le texte d'une facture.

    La dernière occurrence de l'étiquette la plus prioritaire est retenue
    (le total général est en fin de document) ; le montant peut être sur
    la ligne de l'étiquette ou sur la suivante. Renvoie `None` si aucune
    étiquette ni aucun montant n'est trouvé.
    """
    # Normalisation : insécables → espace, pour unifier les montants.
    lignes = [ligne.replace("\u00a0", " ").replace("\u202f", " ") for ligne in texte.splitlines()]
    for etiquette in _ETIQUETTES:
        motif = re.compile(etiquette, re.IGNORECASE)
        total = _chercher_apres_etiquette(motif, lignes)
        if total is not None:
            return total
    return None


def _chercher_apres_etiquette(motif: re.Pattern[str], lignes: list[str]) -> float | None:
    """Dernier montant qui suit l'étiquette, sur sa ligne ou la suivante."""
    resultats: list[float] = []
    for index, ligne in enumerate(lignes):
        if _LIGNE_SOUS_TOTAL.search(ligne):
            continue
        trouve = motif.search(ligne)
        if trouve is None:
            continue
        montant = _premier_montant(ligne[trouve.end() :])
        if montant is None and index + 1 < len(lignes):
            montant = _premier_montant(lignes[index + 1])
        if montant is not None:
            resultats.append(montant)
    return resultats[-1] if resultats else None


def _premier_montant(texte: str) -> float | None:
    """Analyse le premier montant français du texte (centimes exigés)."""
    trouve = _MONTANT_RE.search(texte)
    if trouve is None:
        return None
    brut = trouve.group(0)
    epure = re.sub(r"[\u00a0\u202f .]", "", brut).replace(",", ".")
    return float(epure)


def total_ttc_du_pdf(pdf: bytes) -> float | None:
    """Extrait le total TTC d'un PDF de facture (téléchargé au préalable)."""
    return extraire_total_ttc(texte_du_pdf(pdf))
