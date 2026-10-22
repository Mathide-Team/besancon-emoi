# Journal des versions

## [0.3.0] - 2026-10-22

### Ajouté
- **Journal de debug complet** (#24, SOCLE §8) : chaque fonction de l'intégration trace son entrée (paramètres résumés ; courriel, mot de passe et session masqués) et chacune de ses sorties, et chaque exception interceptée est journalisée. Activation : `logger: logs: custom_components.besancon_emoi: debug`.

### Modifié
- Un journal par module (`custom_components.besancon_emoi.api`, `.coordinator`…) au lieu d'un journal commun nommé d'après `const`.
- Scripts journalisés avec loguru ; `tester_hors_ha.py` y renvoie aussi le journal du client.

## [0.2.0] - 2026-10-20

### Ajouté

- Capteur **Dernière facture (TTC)** par contrat d'eau : total TTC extrait
  du PDF de la facture (le portail ne publie que le montant prélevé).
  Lecture unique par facture (cache par numéro), échec non bloquant (#5).
- Saisie hebdomadaire sur les comptes à plusieurs contrats d'eau :
  appariement « contrat → entité source » dans les options (un champ par
  contrat), garde-fous conservés (index absolu en m³) ; sans appariement,
  le comportement historique est conservé (#4).

## [0.1.0] - 2026-10-18

### Ajouté

- Intégration « Besançon et moi » (besancon-emoi.fr) : connexion, contrats
  d'eau, historique d'index et prélèvements.
- Capteurs par contrat : index du compteur, consommation moyenne
  journalière, dernier prélèvement (avec n° de facture et lien du PDF).
- Action `saisir_index` (saisie manuelle, ciblage par contrat) et saisie
  hebdomadaire automatique depuis une entité source (index absolu en m³).
- Ré-authentification via le flux de configuration.
