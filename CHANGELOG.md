# Journal des versions

## [Non publié]

### Ajouté

- Capteur **Dernière facture (TTC)** par contrat d'eau : total TTC extrait
  du PDF de la facture (le portail ne publie que le montant prélevé).
  Lecture unique par facture (cache par numéro), échec non bloquant (#5).

## [0.1.0] - 2026-10-18

### Ajouté

- Intégration « Besançon et moi » (besancon-emoi.fr) : connexion, contrats
  d'eau, historique d'index et prélèvements.
- Capteurs par contrat : index du compteur, consommation moyenne
  journalière, dernier prélèvement (avec n° de facture et lien du PDF).
- Action `saisir_index` (saisie manuelle, ciblage par contrat) et saisie
  hebdomadaire automatique depuis une entité source (index absolu en m³).
- Ré-authentification via le flux de configuration.
