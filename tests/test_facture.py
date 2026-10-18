"""Tests de la lecture du total TTC dans le PDF d'une facture (#5).

Les factures réelles ne sont pas disponibles dans le dépôt : l'extraction
est testée sur des PDF synthétiques construits avec pypdf, dont le texte
reprend les intitulés attendus d'une facture d'eau française. La
validation sur plusieurs factures réelles reste à faire (issue #5).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.api import (
    BesanconEmoiError,
    Bill,
    Contract,
    ContractData,
    IndexReading,
)
from custom_components.besancon_emoi.const import DOMAIN
from custom_components.besancon_emoi.facture import (
    extraire_total_ttc,
    texte_du_pdf,
    total_ttc_du_pdf,
)

EMAIL = "exemple@fr.fr"
PASSWORD = "secret"
IDCONTRAT = "111111-02"
IDCONTRAT_SLUG = "111111_02"
ABO = "99999"


def _pdf_avec_texte(lignes: list[str]) -> bytes:
    """PDF minimal d'une page, valide, avec les lignes de texte posées.

    Construit à la main (catalogue, pages, page, contenu, police Helvetica
    WinAnsi) : pypdf sait relire le texte exactement posé.
    """
    contenu = b"BT /F1 10 Tf 72 770 Td 16 TL\n"
    for ligne in lignes:
        texte = ligne.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        contenu += f"({texte}) Tj T*\n".encode("latin-1")
    contenu += b"ET\n"

    objets: list[bytes] = []
    objets.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objets.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objets.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n"
    )
    objets.append(
        f"4 0 obj\n<< /Length {len(contenu)} >>\nstream\n".encode() + contenu + b"endstream\nendobj\n"
    )
    objets.append(
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>\nendobj\n"
    )

    pdf = b"%PDF-1.4\n"
    positions = []
    for objet in objets:
        positions.append(len(pdf))
        pdf += objet
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objets) + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for position in positions:
        pdf += f"{position:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    return pdf


def test_total_ttc_sur_la_meme_ligne() -> None:
    """« TOTAL TTC 45,60 € » sur une ligne est lu tel quel."""
    texte = "Consommation 12,00 €\nTVA 10 % 1,20 €\nTOTAL TTC 45,60 €"
    assert extraire_total_ttc(texte) == 45.60


def test_total_ttc_ligne_suivante() -> None:
    """L'étiquette et le montant peuvent être sur deux lignes."""
    texte = "TOTAL TTC\n123,45 €"
    assert extraire_total_ttc(texte) == 123.45


def test_total_avec_separateur_de_milliers() -> None:
    """« 1 234,56 » (insécable ou fine) est converti en 1234.56."""
    assert extraire_total_ttc("TOTAL TTC 1\u00a0234,56 €") == 1234.56
    assert extraire_total_ttc("TOTAL TTC 1\u202f234,56 €") == 1234.56
    assert extraire_total_ttc("TOTAL TTC 1.234,56 €") == 1234.56


def test_derniere_occurrence_retenue() -> None:
    """Le total général (dernier « TOTAL TTC ») prime sur un récapitulatif."""
    texte = "Prestation 1\nTOTAL TTC 10,00 €\nPrestation 2\nTOTAL TTC 20,00 €"
    assert extraire_total_ttc(texte) == 20.00


def test_sous_total_ignore() -> None:
    """Une ligne « sous-total » n'est pas prise pour le total."""
    texte = "Sous-total TTC 10,00 €\nAcompte 5,00 €\nNET À PAYER 5,00 €"
    assert extraire_total_ttc(texte) == 5.00


def test_repli_net_a_payer() -> None:
    """Sans « TOTAL TTC », « NET À PAYER » (ou A) fait office de total."""
    assert extraire_total_ttc("NET À PAYER 60,00 €") == 60.00
    assert extraire_total_ttc("TOTAL A PAYER\n77,70 €") == 77.70


def test_aucun_total_renvoie_none() -> None:
    """Sans étiquette connue ni montant, l'extraction échoue proprement."""
    assert extraire_total_ttc("Facture du mois de janvier") is None
    assert extraire_total_ttc("TOTAL TTC (aucun montant)") is None


def test_tva_non_prise_pour_le_total() -> None:
    """« TVA 10 % » n'est jamais un montant de total (centimes exigés)."""
    assert extraire_total_ttc("TVA 10 %\nTOTAL TTC 50,00 €") == 50.00


def test_texte_du_pdf_et_extraction_complete() -> None:
    """Le PDF synthétique est relu par pypdf et le total en est extrait."""
    pdf = _pdf_avec_texte(
        [
            "Facture 70001 du 15/09/2026",
            "Consommation d'eau 12,00 EUR",
            "TVA 10 % 1,20 EUR",
            "TOTAL TTC 45,60 EUR",
        ]
    )
    texte = texte_du_pdf(pdf)
    assert "TOTAL TTC" in texte
    assert total_ttc_du_pdf(pdf) == 45.60


def test_texte_du_pdf_refuse_les_non_octets() -> None:
    """Un contenu qui n'est pas des octets est refusé avec ValueError."""
    try:
        texte_du_pdf("ceci n'est pas un pdf")  # type: ignore[arg-type]
    except ValueError as err:
        assert "invalide" in str(err)
    else:
        raise AssertionError("ValueError attendue")


def test_total_ttc_du_pdf_sur_document_illisible() -> None:
    """Un PDF abîmé lève ValueError (le coordinateur journalisera)."""
    try:
        texte_du_pdf(b"%PDF-1.4 pas du tout un pdf valide")
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError attendue")


def _contract_data(bill: Bill) -> ContractData:
    return ContractData(
        contract=Contract(idcontrat=IDCONTRAT, abo=ABO, last_index=322),
        index_history=[IndexReading(date=date(2026, 8, 18), index=322, releve="Relevé", conso_jour=0.241)],
        bills=[bill],
    )


def _bill(pdf_disponible: bool = True) -> Bill:
    return Bill(
        facture="70001",
        date=date(2026, 9, 15),
        contrat=IDCONTRAT,
        montant_preleve=-0.99,
        pdf_path="/facture.pdf" if pdf_disponible else None,
    )


async def _configurer_avec_pdf(hass: HomeAssistant, pdf: bytes) -> tuple[MockConfigEntry, AsyncMock]:
    """Installe l'intégration avec une facture dont le PDF est servi."""
    hass.config.language = "fr"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    bill = _bill()
    contract_data = _contract_data(bill)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[contract_data.contract])
        instance.async_get_index_history = AsyncMock(return_value=contract_data.index_history)
        instance.async_get_bills = AsyncMock(return_value=contract_data.bills)
        instance.async_download_bill_pdf = AsyncMock(return_value=pdf)
        instance.async_submit_index = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, instance


async def test_capteur_derniere_facture_ttc(hass: HomeAssistant) -> None:
    """Le total TTC extrait du PDF alimente le capteur « Dernière facture »."""
    pdf = _pdf_avec_texte(
        [
            "Facture 70001",
            "Consommation 40,00 EUR",
            "TOTAL TTC 45,60 EUR",
        ]
    )
    entry, instance = await _configurer_avec_pdf(hass, pdf)
    state = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_derniere_facture_ttc")
    assert state is not None
    assert state.state == "45.6"
    assert state.attributes["facture"] == "70001"
    assert state.attributes["montant_preleve"] == -0.99
    assert state.attributes["lien_facture"] == "/facture.pdf"
    # Le PDF n'est téléchargé qu'une fois : le cache par facture joue.
    coordinator = entry.runtime_data
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    assert instance.async_download_bill_pdf.await_count == 1


async def test_capteur_inconnu_si_pdf_inaccessible(hass: HomeAssistant) -> None:
    """Un PDF inaccessible n'interrompt pas la mise à jour : capteur inconnu."""
    hass.config.language = "fr"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    bill = _bill()
    contract_data = _contract_data(bill)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[contract_data.contract])
        instance.async_get_index_history = AsyncMock(return_value=contract_data.index_history)
        instance.async_get_bills = AsyncMock(return_value=contract_data.bills)
        instance.async_download_bill_pdf = AsyncMock(
            side_effect=BesanconEmoiError("Téléchargement de la facture impossible (500)")
        )
        instance.async_submit_index = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_derniere_facture_ttc")
    assert state is not None
    assert state.state == "unknown"
    # Les autres capteurs sont bien là : la mise à jour a réussi.
    index = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_index_du_compteur")
    assert index is not None and index.state == "322"
