"""Bibliothèque de mods : import (zip / 7z / dossier), extraction, stockage.

Organisation sur disque :

    <root>/<game>/<mod_id>/manifest.json
    <root>/<game>/<mod_id>/files/...        contenu extrait, structure d'origine
    <root>/.tmp/                            zone de travail (même disque -> rename atomique)

Le dossier du jeu n'est jamais touché ici : c'est le rôle de `linker.py`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ..games.base import Game
from .manifest import MANIFEST_NAME, Manifest, ManifestFile, read_manifest, write_manifest

FILES_DIR = "files"
_JUNK_NAMES = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}
_SEVENZIP_SUFFIXES = {".7z", ".rar"}

# "Fast Travel Anywhere-1234-1-2-1699999999.zip" -> nom, id Nexus, version, timestamp
_NEXUS_RE = re.compile(r"^(?P<name>.+?)-(?P<nid>\d+)(?:-(?P<ver>[\w.]+(?:-[\w.]+)*?))?-(?P<ts>\d{9,11})$")


class ModImportError(Exception):
    """Erreur d'import d'un mod (message destiné à l'utilisateur)."""


class ModExistsError(ModImportError):
    def __init__(self, mod_id: str):
        super().__init__(f"Le mod « {mod_id} » existe déjà (utiliser replace=True pour le remplacer).")
        self.mod_id = mod_id


class Library:
    def __init__(self, root: Path):
        # Absolu : les symlinks créés dans le jeu pointent ici, quel que soit le dossier courant.
        self.root = Path(root).expanduser().resolve()

    # ----------------------------------------------------------------- lecture

    def mod_dir(self, game_id: str, mod_id: str) -> Path:
        return self.root / game_id / mod_id

    def files_dir(self, game_id: str, mod_id: str) -> Path:
        return self.mod_dir(game_id, mod_id) / FILES_DIR

    def get(self, game_id: str, mod_id: str) -> Manifest:
        return read_manifest(self.mod_dir(game_id, mod_id))

    def list_mods(self, game_id: str) -> list[Manifest]:
        game_root = self.root / game_id
        if not game_root.is_dir():
            return []
        return [
            read_manifest(d)
            for d in sorted(game_root.iterdir())
            if (d / MANIFEST_NAME).is_file()
        ]

    # ------------------------------------------------------------------ import

    def import_mod(
        self,
        source: Path,
        game: Game,
        *,
        name: str | None = None,
        version: str | None = None,
        source_url: str | None = None,
        labels: list[str] | None = None,
        replace: bool = False,
    ) -> Manifest:
        """Importe un mod (zip, 7z/rar, ou dossier) dans la bibliothèque et écrit son manifeste."""
        source = Path(source)
        if not source.exists():
            raise ModImportError(f"Introuvable : {source}")

        meta = parse_nexus_filename(source.stem if source.is_file() else source.name)
        name = name or meta["name"]
        version = version or meta["version"]
        nexus_id = meta["nexus_id"]
        if source_url is None and nexus_id is not None:
            source_url = game.nexus_mod_url(nexus_id)

        mod_id = slugify(name)
        if not mod_id:
            raise ModImportError(f"Impossible de déduire un identifiant depuis « {name} ».")
        mod_dir = self.mod_dir(game.id, mod_id)
        if mod_dir.exists() and not replace:
            raise ModExistsError(mod_id)

        tmp_root = self.root / ".tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"{mod_id}-", dir=tmp_root))
        try:
            staged = work / FILES_DIR
            staged.mkdir()
            _extract(source, staged)
            _remove_junk(staged)

            rel_paths = sorted(
                PurePosixPath(p.relative_to(staged).as_posix()) for p in staged.rglob("*") if p.is_file()
            )
            if not rel_paths:
                raise ModImportError("Le mod est vide.")

            mapping = game.map_files(rel_paths, mod_id)
            warnings = _check_duplicates(mapping.files, staged) + _suspicious_ignored(mapping.ignored)

            dsts = [f.dst for f in mapping.files]
            all_labels = list(dict.fromkeys([*(labels or []), *game.auto_labels(dsts)]))
            manifest = Manifest(
                id=mod_id,
                game=game.id,
                name=name,
                version=version,
                source=source_url,
                nexus_id=nexus_id,
                original_filename=source.name,
                installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                requires=game.detect_requirements(dsts),
                provides=game.detect_provides(dsts),
                labels=all_labels,
                files=[ManifestFile(f.src, f.dst) for f in mapping.files],
                ignored=mapping.ignored,
                warnings=warnings,
            )
            write_manifest(work, manifest)

            # Bascule finale : le mod n'apparaît dans la bibliothèque qu'une fois complet.
            mod_dir.parent.mkdir(parents=True, exist_ok=True)
            if mod_dir.exists():
                old = work.with_name(work.name + ".old")
                mod_dir.rename(old)
                work.rename(mod_dir)
                shutil.rmtree(old)
            else:
                work.rename(mod_dir)
            return manifest
        finally:
            if work.exists():
                shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------- utils


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_nexus_filename(stem: str) -> dict:
    """Déduit nom / id Nexus / version d'un nom de fichier téléchargé depuis Nexus.

    "Fast Travel Anywhere-1234-1-2-1699999999" -> name="Fast Travel Anywhere", nexus_id=1234, version="1.2"
    Tout autre nom est gardé tel quel comme nom du mod.
    """
    m = _NEXUS_RE.match(stem)
    if not m:
        return {"name": stem.strip(), "nexus_id": None, "version": None}
    ver = m.group("ver")
    return {
        "name": m.group("name").strip(" -_"),
        "nexus_id": int(m.group("nid")),
        "version": ver.replace("-", ".") if ver else None,
    }


def _extract(source: Path, dest: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=False)
    elif zipfile.is_zipfile(source):
        _safe_unzip(source, dest)
    elif source.suffix.lower() in _SEVENZIP_SUFFIXES:
        _extract_7z(source, dest)
    else:
        raise ModImportError(f"Format non supporté : {source.name} (zip, 7z, rar ou dossier attendu).")
    _reject_escaping_links(dest)


def _safe_unzip(source: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(source) as zf:
        for info in zf.infolist():
            # Les zips Windows utilisent parfois "\" comme séparateur.
            name = info.filename.replace("\\", "/")
            rel = PurePosixPath(name)
            if rel.is_absolute() or ".." in rel.parts or (rel.parts and rel.parts[0].endswith(":")):
                raise ModImportError(f"Chemin dangereux dans l'archive : {info.filename}")
            target = dest / rel
            if not target.resolve().is_relative_to(dest_resolved):
                raise ModImportError(f"Chemin dangereux dans l'archive : {info.filename}")
            if info.is_dir() or name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _extract_7z(source: Path, dest: Path) -> None:
    exe = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    if exe is None:
        raise ModImportError(
            f"{source.name} : installe p7zip / 7-Zip pour importer les archives {source.suffix} "
            "(ou décompresse-la et importe le dossier)."
        )
    # -snl- : ne pas recréer de liens symboliques ; -y : pas de questions.
    result = subprocess.run(
        [exe, "x", "-y", "-snl-", f"-o{dest}", str(source)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ModImportError(f"Échec de l'extraction de {source.name} :\n{result.stderr.strip() or result.stdout.strip()}")


def _reject_escaping_links(dest: Path) -> None:
    for p in dest.rglob("*"):
        if p.is_symlink():
            raise ModImportError(f"Lien symbolique interdit dans le mod : {p.relative_to(dest)}")


def _remove_junk(root: Path) -> None:
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if p.name.lower() in _JUNK_NAMES and p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()


def _check_duplicates(files, staged: Path) -> list[str]:
    """Deux fichiers du même mod qui iraient au même endroit : en général un installeur FOMOD."""
    seen: dict[str, str] = {}
    dupes = []
    for f in files:
        key = f.dst.lower()
        if key in seen:
            dupes.append(f"{seen[key]} / {f.src} -> {f.dst}")
        else:
            seen[key] = f.src
    fomod = any(p.name.lower() == "moduleconfig.xml" for p in staged.rglob("*"))
    if not dupes:
        return ["Installeur FOMOD présent : vérifie que la bonne variante a été installée."] if fomod else []
    hint = (
        "Installeur FOMOD détecté : le mod propose plusieurs variantes. "
        "Choix des options pas encore géré, importe le sous-dossier voulu."
        if fomod
        else "Le mod contient plusieurs fichiers pour la même destination (variantes ?)."
    )
    raise ModImportError(hint + "\n  " + "\n  ".join(dupes[:10]))


_CODE_SUFFIXES = {".dll", ".asi", ".lua", ".reds", ".archive", ".xl", ".yaml", ".yml", ".tweak", ".exe", ".ini", ".json"}


def _suspicious_ignored(ignored: list[str]) -> list[str]:
    """Fichiers de code / données ignorés : le mod a peut-être une structure non reconnue."""
    hits = [
        p for p in ignored
        if PurePosixPath(p).suffix.lower() in _CODE_SUFFIXES and "fomod" not in (q.lower() for q in PurePosixPath(p).parts)
    ]
    if not hits:
        return []
    shown = ", ".join(hits[:5]) + (" ..." if len(hits) > 5 else "")
    return [f"{len(hits)} fichier(s) non installé(s) car leur emplacement est inconnu : {shown}"]
