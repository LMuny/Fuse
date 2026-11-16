# Fuse — Portail de gestion de mods

Outil local pour importer, activer/désactiver, diagnostiquer et supprimer proprement des mods de jeux. Premier jeu cible : **Cyberpunk 2077**. Conçu pour supporter d'autres jeux ensuite.

## Objectifs

- Plus facile d'accès
- Plus propre (le dossier du jeu n'est jamais pollué par des fichiers orphelins)
- Nettoyage simple et exact

## Principe

1. On télécharge un mod sur Nexus (zip ou dossier).
2. On le dépose dans Fuse.
3. Fuse l'extrait dans sa propre bibliothèque et génère un manifeste.
4. Fuse "branche" le mod dans le jeu via des **symlinks** (ou hardlinks / junctions sous Windows).
5. Activer / désactiver = créer / supprimer les liens. Supprimer = effacer le dossier du mod + ses liens.

## Fonctionnalités

- Import zip ou dossier
- Liste des mods avec activer / désactiver / supprimer
- Labels pour catégoriser les mods (filtrables)
- Détection des conflits de fichiers (deux mods qui écrasent le même fichier)
- Détection des dépendances manquantes (CET, RED4ext, ArchiveXL, TweakXL, Codeware, redscript...)
- Lecture des logs pour repérer les mods qui crashent ou bloquent
- Bissection semi-automatique (désactiver la moitié des mods, relancer, etc.)
- Profils / backup avant chaque modification

## Détection des mods fautifs

Sources de logs à lire (Cyberpunk 2077) :

- `r6/cache`
- `red4ext/logs`
- `bin/x64/plugins/cyber_engine_tweaks/*.log`
- logs REDmod / redscript (indiquent souvent le fichier fautif)

Limite : un crash silencieux n'est pas toujours attribuable à un mod précis.

## Points de vigilance

- **Nexus** : pas de téléchargement automatique sans API / Premium. Le glisser-déposer manuel est la bonne approche (l'API Nexus pourra venir plus tard).
- **Linux / Proton** : CP2077 tourne via le préfixe Wine (`compatdata/1091500/pfx/...`). Les symlinks fonctionnent bien.
- **Windows** : les symlinks demandent des droits. Alternatives : hardlinks ou junctions.
- Certains mods ont des étapes spécifiques (REDmod, `.archive` vs scripts).

## Stack

- Python 3
- Flask ou FastAPI pour une interface web locale (ou PyQt / Tkinter pour du natif)
- SQLite pour les données

## Structure du projet

```
fuse/
├── core/
│   ├── library.py      # import zip/dossier, extraction, stockage
│   ├── manifest.py     # lecture/écriture des manifestes
│   ├── linker.py       # symlinks : enable/disable
│   ├── conflicts.py    # détection de fichiers en double
│   └── labels.py       # gestion des labels
├── games/
│   ├── base.py         # classe Game (interface)
│   ├── cyberpunk2077.py
│   └── skyrim.py       # plus tard
├── web/                # Flask/FastAPI + templates
├── data/fuse.db        # SQLite
└── library/<game>/<mod_id>/   # fichiers extraits
```

## Architecture multi-jeux

Le **cœur** est générique. Chaque jeu est un **adaptateur** (un fichier) qui implémente l'interface `Game` :

| Élément | Rôle |
|---|---|
| `detect_install_path()` | Trouver le jeu (Steam, Proton, GOG...) |
| `target_dirs` | Où les fichiers vont (`archive/pc/mod/`, `r6/scripts/`...) |
| `required_frameworks` | Dépendances (CET, RED4ext, ArchiveXL...) |
| `log_paths` + `parse_logs()` | Repérer les mods fautifs |

## Base de données (SQLite)

```
mods(id, game, name, version, source_url, enabled, installed_at)
files(mod_id, rel_path, target_path)
labels(id, name, color)
mod_labels(mod_id, label_id)
```

## Manifeste d'un mod

Écrit aussi en `manifest.json` dans le dossier du mod, pour pouvoir reconstruire la base.

```json
{
  "id": "fast-travel-anywhere",
  "game": "cyberpunk2077",
  "name": "Fast Travel Anywhere",
  "version": "1.2",
  "source": "https://nexusmods.com/cyberpunk2077/mods/...",
  "requires": ["cet", "redscript"],
  "labels": ["gameplay", "qol"],
  "files": [
    {"src": "archive/pc/mod/ft.archive", "dst": "archive/pc/mod/ft.archive"}
  ]
}
```

## Labels

- Table many-to-many : un mod peut avoir plusieurs labels.
- Un label = un nom + une couleur.
- Filtrage dans la liste des mods.
- Labels automatiques possibles selon le dossier cible (`archive/` → « visuel », `r6/scripts/` → « script »).

## Plan MVP

1. Import zip / dossier → extraction + manifeste
2. Liste des mods avec activer / désactiver / supprimer (symlinks)
3. Labels
4. Détection de conflits de fichiers
5. Lecture des logs pour repérer les mods fautifs
6. Second jeu pour valider l'architecture multi-jeux

## Avancement

- [x] `core/library.py` + `core/manifest.py` + `games/cyberpunk2077.py` : import zip / 7z / dossier, manifeste, détection des dépendances, labels auto (`python -m fuse import <fichier>`)
- [x] `core/linker.py` : activer / désactiver / supprimer / vérifier (`fuse set-path`, `enable`, `disable`, `remove`, `verify`)
  - État dans `library/<jeu>/state.json` : liens posés, fichiers d'origine sauvegardés, dossiers créés par Fuse
  - Un fichier du jeu remplacé par un mod est mis de côté dans `library/<jeu>/.backup/` puis restauré
  - Activation tout-ou-rien ; un conflit avec un mod actif bloque l'activation
  - Symlinks, hardlinks en secours (Windows sans mode développeur)

Tests : `python3 -m unittest discover -s tests -t .`

## Prochaine étape

Gestion des conflits (étape 4) : ordre de priorité entre mods quand deux fournissent le même fichier, au lieu de simplement refuser. Puis vérification des dépendances manquantes (`requires` vs `provides` des mods actifs).
