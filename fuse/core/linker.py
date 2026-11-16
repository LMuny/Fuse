"""Branchement des mods dans le dossier du jeu (symlinks, hardlinks en secours).

Fuse ne touche au dossier du jeu qu'ici, et note tout ce qu'il y fait dans
`<library>/<game>/state.json` :

- `links`        : chaque fichier lié -> mod propriétaire + méthode (symlink / hardlink)
- `backups`      : fichiers d'origine déplacés de côté parce qu'un mod les remplace
- `created_dirs` : dossiers créés par Fuse (supprimés quand ils redeviennent vides)

Désactiver un mod = retirer exactement ses liens, restaurer les sauvegardes,
supprimer les dossiers vides que Fuse a créés. Rien d'autre n'est touché.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..games.base import Game
from .library import Library

STATE_NAME = "state.json"
BACKUP_DIR = ".backup"


class LinkError(Exception):
    """Opération de liaison impossible (message destiné à l'utilisateur)."""


class ConflictError(LinkError):
    def __init__(self, mod_id: str, conflicts: dict[str, str]):
        self.mod_id = mod_id
        self.conflicts = conflicts  # dst -> mod déjà propriétaire
        lines = [f"  {dst}  (déjà fourni par « {owner} »)" for dst, owner in sorted(conflicts.items())[:10]]
        more = f"\n  ... et {len(conflicts) - 10} autre(s)" if len(conflicts) > 10 else ""
        super().__init__(f"« {mod_id} » entre en conflit avec des mods actifs :\n" + "\n".join(lines) + more)


@dataclass
class Problem:
    kind: str  # "missing" | "broken" | "replaced" | "orphan"
    dst: str
    mod: str | None
    detail: str


@dataclass
class State:
    game_path: str | None = None
    enabled: list[str] = field(default_factory=list)
    links: dict[str, dict] = field(default_factory=dict)  # dst -> {"mod", "method"}
    backups: dict[str, str] = field(default_factory=dict)  # dst -> mod
    created_dirs: list[str] = field(default_factory=list)
    schema: int = 1


class Linker:
    def __init__(self, library: Library, game: Game, game_path: Path | None = None):
        self.library = library
        self.game = game
        self.state_path = library.root / game.id / STATE_NAME
        self.state = self._load()
        if game_path is not None:
            self.set_game_path(game_path)

    # ------------------------------------------------------------------- état

    def _load(self) -> State:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return State()
        known = State.__dataclass_fields__.keys()
        return State(**{k: v for k, v in data.items() if k in known})

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state.__dict__, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    @property
    def game_path(self) -> Path:
        if not self.state.game_path:
            raise LinkError(f"Dossier de {self.game.name} inconnu : utilise `fuse set-path <dossier>`.")
        return Path(self.state.game_path)

    def set_game_path(self, path: Path) -> None:
        path = Path(path).expanduser().resolve()
        if self.state.game_path and Path(self.state.game_path) != path and self.state.links:
            raise LinkError(
                f"Des mods sont actifs dans {self.state.game_path} : désactive-les avant de changer de dossier."
            )
        if not self.game.is_valid_install(path):
            raise LinkError(f"{path} ne ressemble pas à une installation de {self.game.name}.")
        self.state.game_path = str(path)
        self._save()

    def is_enabled(self, mod_id: str) -> bool:
        return mod_id in self.state.enabled

    def _backup_root(self) -> Path:
        return self.library.root / self.game.id / BACKUP_DIR

    # ------------------------------------------------------------- activation

    def enable(self, mod_id: str) -> None:
        """Lie tous les fichiers du mod dans le jeu. Tout ou rien."""
        if self.is_enabled(mod_id):
            return
        manifest = self.library.get(self.game.id, mod_id)
        game_root = self.game_path
        files_dir = self.library.files_dir(self.game.id, mod_id)

        owners = {dst.lower(): info["mod"] for dst, info in self.state.links.items()}
        conflicts = {f.dst: owners[f.dst.lower()] for f in manifest.files if f.dst.lower() in owners}
        if conflicts:
            raise ConflictError(mod_id, conflicts)

        # Vérifications avant de toucher à quoi que ce soit.
        to_backup = []
        for f in manifest.files:
            src = files_dir / f.src
            if not src.is_file():
                raise LinkError(f"Fichier manquant dans la bibliothèque : {src}")
            target = game_root / f.dst
            if target.is_symlink() or target.exists():
                if target.is_dir() and not target.is_symlink():
                    raise LinkError(f"Un dossier occupe déjà l'emplacement d'un fichier : {f.dst}")
                to_backup.append(f.dst)

        done_links: list[str] = []
        done_backups: list[str] = []
        new_dirs: list[str] = []
        try:
            for dst in to_backup:
                self._backup(dst)
                done_backups.append(dst)
            for f in manifest.files:
                target = game_root / f.dst
                new_dirs += self._make_parents(target)
                method = _make_link(files_dir / f.src, target)
                done_links.append(f.dst)
                self.state.links[f.dst] = {"mod": mod_id, "method": method}
        except Exception:
            # Retour à l'état exact d'avant.
            for dst in done_links:
                (game_root / dst).unlink(missing_ok=True)
                self.state.links.pop(dst, None)
            for dst in done_backups:
                self._restore(dst)
            for d in reversed(new_dirs):
                _rmdir_if_empty(game_root / d)
            raise

        for dst in done_backups:
            self.state.backups[dst] = mod_id
        self.state.created_dirs += [d for d in new_dirs if d not in self.state.created_dirs]
        self.state.enabled.append(mod_id)
        self._save()

    def disable(self, mod_id: str) -> list[str]:
        """Retire exactement les liens du mod et restaure ce qu'il avait remplacé.

        Retourne des avertissements (sauvegarde non restaurable car l'emplacement est occupé).
        """
        game_root = self.game_path
        owned = [dst for dst, info in self.state.links.items() if info["mod"] == mod_id]

        for dst in owned:
            target = game_root / dst
            if target.is_symlink() or target.is_file():
                if not self._is_ours(dst):
                    # Remplacé par autre chose depuis (mise à jour du jeu, install manuelle) : on n'y touche pas.
                    del self.state.links[dst]
                    continue
                target.unlink()
            del self.state.links[dst]

        warnings: list[str] = []
        for dst, owner in list(self.state.backups.items()):
            if owner != mod_id:
                continue
            if self._restore(dst):
                del self.state.backups[dst]
            else:
                warnings.append(
                    f"{dst} : l'emplacement est occupé, original laissé dans {self._backup_root() / dst}"
                )

        self._cleanup_dirs()
        if mod_id in self.state.enabled:
            self.state.enabled.remove(mod_id)
        self._save()
        return warnings

    def remove(self, mod_id: str) -> list[str]:
        """Désactive puis efface le mod de la bibliothèque."""
        mod_dir = self.library.mod_dir(self.game.id, mod_id)
        if not mod_dir.is_dir():
            raise LinkError(f"Mod introuvable : {mod_id}")
        warnings = []
        if self.is_enabled(mod_id) or any(i["mod"] == mod_id for i in self.state.links.values()):
            warnings = self.disable(mod_id)
        shutil.rmtree(mod_dir)
        return warnings

    # ---------------------------------------------------------- vérification

    def verify(self) -> list[Problem]:
        """Compare l'état noté avec le disque, et cherche des liens orphelins vers la bibliothèque."""
        game_root = self.game_path
        problems: list[Problem] = []
        for dst, info in sorted(self.state.links.items()):
            target = game_root / dst
            mod = info["mod"]
            if not target.is_symlink() and not target.exists():
                problems.append(Problem("missing", dst, mod, "le lien a disparu"))
            elif not self._is_ours(dst):
                problems.append(Problem("replaced", dst, mod, "remplacé par un autre fichier"))
            elif not target.exists():
                problems.append(Problem("broken", dst, mod, "le fichier source n'existe plus"))

        lib_root = self.library.root.resolve()
        known = {d.lower() for d in self.state.links}
        for top in {t.split("/")[0] for t in self.game.target_dirs}:
            base = game_root / top
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if not p.is_symlink():
                    continue
                rel = p.relative_to(game_root).as_posix()
                if rel.lower() in known:
                    continue
                dest = Path(os.readlink(p))
                if not dest.is_absolute():
                    dest = p.parent / dest
                if _is_relative_to(dest, lib_root):
                    problems.append(Problem("orphan", rel, None, f"lien vers la bibliothèque non suivi ({dest})"))
        return problems

    # --------------------------------------------------------------- interne

    def _is_ours(self, dst: str) -> bool:
        info = self.state.links[dst]
        target = self.game_path / dst
        src = self._source_of(info["mod"], dst)
        if src is None:
            return False
        if info["method"] == "symlink":
            return target.is_symlink() and Path(os.readlink(target)) == src
        try:
            return target.exists() and os.path.samefile(target, src)
        except OSError:
            return False

    def _source_of(self, mod_id: str, dst: str) -> Path | None:
        files_dir = self.library.files_dir(self.game.id, mod_id)
        try:
            manifest = self.library.get(self.game.id, mod_id)
        except FileNotFoundError:
            return None
        for f in manifest.files:
            if f.dst == dst:
                return (files_dir / f.src).absolute()
        return None

    def _backup(self, dst: str) -> None:
        target = self.game_path / dst
        backup = self._backup_root() / dst
        if backup.exists() or backup.is_symlink():
            raise LinkError(f"Une sauvegarde existe déjà pour {dst} : état incohérent, vérifie {backup}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(target, backup)

    def _restore(self, dst: str) -> bool:
        """Remet l'original en place. False si l'emplacement est occupé (on n'écrase jamais)."""
        backup = self._backup_root() / dst
        if not (backup.exists() or backup.is_symlink()):
            return True
        target = self.game_path / dst
        if target.exists() or target.is_symlink():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(backup, target)
        _prune_empty(backup.parent, stop=self._backup_root())
        _rmdir_if_empty(self._backup_root())
        return True

    def _make_parents(self, target: Path) -> list[str]:
        """Crée les dossiers parents manquants, retourne ceux créés (relatifs au jeu, du haut vers le bas)."""
        game_root = self.game_path
        missing = []
        p = target.parent
        while not p.exists() and p != game_root:
            missing.append(p)
            p = p.parent
        created = []
        for d in reversed(missing):
            d.mkdir()
            created.append(d.relative_to(game_root).as_posix())
        return created

    def _cleanup_dirs(self) -> None:
        game_root = self.game_path
        # Du plus profond au moins profond, pour vider les arborescences entières.
        for d in sorted(self.state.created_dirs, key=lambda x: x.count("/"), reverse=True):
            path = game_root / d
            if not path.exists():
                self.state.created_dirs.remove(d)
            elif _rmdir_if_empty(path):
                self.state.created_dirs.remove(d)


def _make_link(src: Path, target: Path) -> str:
    try:
        target.symlink_to(src.absolute())
        return "symlink"
    except OSError:
        # Windows sans mode développeur : pas de droit de créer des symlinks.
        try:
            os.link(src, target)
            return "hardlink"
        except OSError as e:
            raise LinkError(
                f"Impossible de lier {target} : {e}. Sous Windows, active le mode développeur "
                "ou place la bibliothèque sur le même disque que le jeu."
            ) from e


def _rmdir_if_empty(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except OSError:
        return False


def _prune_empty(path: Path, stop: Path) -> None:
    while path != stop and _is_relative_to(path, stop) and _rmdir_if_empty(path):
        path = path.parent


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False
