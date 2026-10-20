# Besançon et moi

Intégration Home Assistant pour le portail **« Besançon et moi »**
(besancon-emoi.fr) de la Ville de Besançon et de Grand Besançon Métropole.

Le portail n'expose pas d'API publique : cette intégration s'appuie sur les
pages du site (connexion, contrats d'eau, factures, historique d'index,
saisie du relevé de compteur). Elle est **indépendante de
[tarifeau](https://github.com/Mathilde-Team/tarifeau)** : les deux se
complètent via les entités Home Assistant (tarifeau peut calculer une
facture à partir de l'index publié ici).

## Fonctionnalités

- Connexion à votre espace « Besançon et moi » (e-mail + mot de passe).
- Par contrat d'eau :
  - **Index du compteur** (m³), avec date et type de relevé, et historique ;
  - **Consommation moyenne journalière** (m³/j) ;
  - **Dernier prélèvement** (€) : montant prélevé par le portail
    (mensualité ou ajustement annuel — ce n'est pas le total de la facture),
    avec date, n° de facture et lien du PDF en attributs ;
  - **Dernière facture (TTC)** (€) : total TTC lu dans le PDF de la
    dernière facture (le portail ne le publie pas), avec date,
    n° de facture et montant prélevé en attributs. Le PDF est téléchargé
    une seule fois par facture ; s'il est inaccessible ou illisible, le
    capteur reste inconnu et la mise à jour continue.
- Par contrat déchets (bac roulant) :
  - **Dernière pesée** et **Poids de la dernière pesée** (kg), avec type de
    bac, volume, nombre de vidages, n° de bac et n° de puce en attributs ;
  - **Dernière pesée bac jaune** et **Poids de la dernière pesée bac jaune**
    (kg) pour les bacs recyclables ;
  - la liste des **20 dernières pesées** en attribut de chaque capteur ;
  - la table du portail est paginée : toutes les pages sont lues.
- Calendrier de collecte des déchets (open data Grand Besançon, sans
  identifiants) :
  - **Collecte résiduels** et **Collecte recyclables** (jour + parité
    `toutes les semaines` / `semaines paires` / `semaines impaires`), avec
    prochaine date, compte à rebours en jours, adresse et calendrier PDF
    officiel en attributs ;
  - une entité **calendrier** au format Home Assistant (aperçu du calendrier,
    automations sur les événements) ;
  - configuration par commune et rue du formulaire
    [open data](https://data.grandbesancon.fr/opendata/dataset/joursDeCollecteDechets/)
    (68 communes), dans les options de l'intégration ;
  - les erreurs open data n'interrompent pas les autres entités.
- Action `besancon_emoi.saisir_index` pour transmettre un relevé de compteur,
  avec ciblage par contrat quand plusieurs compteurs sont configurés.
- **Saisie hebdomadaire automatique** : l'index est lu sur une entité de
  votre choix (par exemple le compteur wM-Bus d'
  [Esphome-Compteur-wmbus](https://github.com/Mathilde-Team/Esphome-Compteur-wmbus))
  et transmis au portail une fois par semaine, le jour choisi.

## Installation

### HACS (recommandé)

1. Ajoutez ce dépôt dans HACS (« Dépôts personnalisés », catégorie
   « Intégration »).
2. Recherchez « Besançon et moi » et installez-la.
3. Redémarrez Home Assistant.

### Manuel

Copiez `custom_components/besancon_emoi` dans votre dossier `config/custom_components/`.

## Configuration

1. Réglages → Appareils et services → Ajouter une intégration →
   « Besançon et moi ».
2. Saisissez l'e-mail et le mot de passe de votre compte besancon-emoi.fr.

### Options

- **Saisie hebdomadaire automatique** : active la transmission automatique.
- **Jour de la semaine** : jour d'envoi du relevé.
- **Entité source de l'index** : `entity_id` du capteur qui publie l'index
  absolu du compteur (en m³).
- **Entité source par contrat** (comptes à plusieurs contrats d'eau) : un
  champ « index_entity::<contrat> » par contrat connu ; la saisie
  hebdomadaire transmet à chaque compteur l'index de sa propre entité.
  Sans appariement, l'automatisme reste limité au compte à contrat unique
  (jamais le même index envoyé à plusieurs compteurs).

### Exemple d'automatisation (saisie manuelle hebdomadaire)

```yaml
automation:
  - alias: Relevé hebdomadaire du compteur d'eau
    triggers:
      - trigger: time
        at: "08:00:00"
    conditions:
      - condition: time
        weekday: mon
    actions:
      - action: besancon_emoi.saisir_index
        data:
          index: "{{ states('sensor.compteur_eau_index') | int }}"
          contrat: "123456-02"  # obligatoire si plusieurs contrats
```

## Tableau de bord

Une vue prête à l'emploi est fournie dans
[`exemples/tableau-de-bord.yaml`](exemples/tableau-de-bord.yaml) :

- **prochaines collectes** : jour et échéance (aujourd'hui, demain, dans
  N jours) des résiduels et des recyclables, adresse et lien vers le
  calendrier PDF ;
- **mes contrats** : un tableau par contrat (eau, déchets) avec toutes ses
  mesures, sans rien à adapter ;
- **calendrier de collecte** et **consommation d'eau sur 30 jours** :
  remplacer les entités d'exemple par les vôtres.

Seules des cartes intégrées à Home Assistant sont utilisées. Installation :
Paramètres → Tableaux de bord → ouvrir un tableau de bord → ⋮ → Modifier
→ ⋮ → Éditeur de configuration, puis coller le contenu du fichier comme
nouvel élément de `views:`.

## Limites

- Le portail est un site web : toute évolution de son HTML peut casser
  l'intégration (les tests capturent la structure observée).
- La facture reste disponible en PDF sur le portail ; l'intégration publie
  le montant prélevé et les dates, pas le document (le lien relatif du PDF
  est fourni en attribut du capteur).
- La saisie hebdomadaire automatique prend en charge plusieurs contrats
  d'eau via l'appariement explicite « contrat → entité source » dans les
  options (un champ par contrat) ; sans appariement, elle ne cible qu'un
  compte à contrat unique (jamais le même index envoyé à plusieurs
  compteurs) et l'action manuelle exige le champ « contrat ».
- L'entité source d'index doit publier l'index **absolu** du compteur en m³
  (pas une consommation périodique ni des litres).
- Le mot de passe du portail est stocké par Home Assistant comme pour toute
  intégration à identifiants ; la session est dédiée à chaque compte.
- Aucune donnée n'est envoyée ailleurs qu'au portail officiel.

## Développement

```bash
uv sync
uv run --only-group lint ruff check .
uv run -p 3.13 --only-group test pytest --cov
```

## Socle qualité Mathilde-Team

Ce dépôt suit le [socle qualité de l'équipe](https://github.com/Mathilde-Team/.github/blob/main/SOCLE.md) :
branches protégées `dev` (intégration) et `main` (livraison), CI requise
(ruff, yamllint, pytest, hassfest, validation HACS), licence MIT.
