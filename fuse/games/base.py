"""Interface commune à tous les jeux supportés par Fuse.

Le cœur (library, linker, conflicts...) est générique ; tout ce qui dépend
d'un jeu (où il est installé, où vont les fichiers, quelles dépendances)
vit dans un adaptateur qui hérite de `Game`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class LayoutError(Exception):
    """Le contenu du mod ne correspond à aucune structure reconnue."""


@dataclass(frozen=True)
class FileMapping:
    """Un fichier du mod : `src` (relatif au dossier extrait) -> `dst` (relatif au dossier du jeu)."""

    src: str
    dst: str


@dataclass(frozen=True)
class Framework:
    """Une dépendance « framework » (CET, RED4ext...)."""

    id: str
    name: str
    nexus_url: str | None = None


@dataclass
class MappingResult:
    files: list[FileMapping]
    ignored: list[str]  # fichiers gardés dans la bibliothèque mais jamais liés (readme, images...)


class Game(ABC):
    id: str
    name: str
    nexus_domain: str | None = None
    target_dirs: tuple[str, ...] = ()
    required_frameworks: dict[str, Framework] = {}
    log_paths: tuple[str, ...] = ()

    @abstractmethod
    def detect_install_path(self) -> Path | None:
        """Trouve le dossier d'installation du jeu (Steam, Proton, GOG...)."""

    @abstractmethod
    def map_files(self, rel_paths: list[PurePosixPath], mod_id: str) -> MappingResult:
        """Décide où chaque fichier extrait doit aller dans le dossier du jeu.

        Lève `LayoutError` si rien d'installable n'est trouvé.
        """

    def detect_requirements(self, dsts: list[str]) -> list[str]:
        """Ids des frameworks dont le mod a besoin."""
        return []

    def detect_provides(self, dsts: list[str]) -> list[str]:
        """Ids des frameworks que ce mod fournit lui-même (ex : le mod CET)."""
        return []

    def auto_labels(self, dsts: list[str]) -> list[str]:
        """Labels déduits des dossiers cibles."""
        return []

    def is_valid_install(self, path: Path) -> bool:
        return path.is_dir()

    def nexus_mod_url(self, nexus_id: int) -> str | None:
        if not self.nexus_domain:
            return None
        return f"https://www.nexusmods.com/{self.nexus_domain}/mods/{nexus_id}"
