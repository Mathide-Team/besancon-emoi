# Nom du projet

<!-- Remplacer ce README. La partie « Socle qualité » peut rester telle quelle. -->

## Socle qualité Mathide-Team

Ce dépôt est créé depuis [`modele-depot`](https://github.com/Mathide-Team/modele-depot). Il suit le [socle qualité de l'équipe](https://github.com/Mathide-Team/.github/blob/main/SOCLE.md) :

- **`main`** (livraison) et **`dev`** (intégration) protégées. On n'y pousse jamais directement : PR vers `dev`, puis PR `dev` → `main`.
- **CI** : job « Qualité (lint, tests) », requis sur les deux branches. À adapter au langage du projet dans `.github/workflows/ci.yml`, en gardant le nom du job.
- **Garde** : `.github/workflows/guard-main.yml` refuse les PR vers `main` qui ne viennent pas de `dev`.
- **Protections versionnées** : `.github/branch-protection/{main,dev}.json`, vérifiées chaque jour par le workflow « Protection des branches ».
- **Licence** : [MIT](LICENSE).

### Après « Use this template »

Les protections ne sont pas copiées par GitHub avec le modèle. Un administrateur les applique une fois :

```bash
gh repo create Mathide-Team/<nom> --public --template Mathide-Team/modele-depot --clone
cd <nom>
scripts/appliquer_socle.sh Mathide-Team/<nom>     # crée dev + applique les protections
```

Puis pour chaque sujet :

```bash
git switch dev && git pull && git switch -c feat/<n°>-description
# …
gh pr create --base dev
```
