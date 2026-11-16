"""Adaptateur Cyberpunk 2077."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePosixPath

from .base import FileMapping, Framework, Game, LayoutError, MappingResult

# Dossier racine -> sous-dossiers attendus juste en dessous (None = n'importe lequel).
# Sert à reconnaître la racine du jeu à l'intérieur d'un zip, même enveloppée
# dans un dossier "NomDuMod/" ou "NomDuMod-1.2/".
_ROOTS: dict[str, set[str] | None] = {
    "archive": {"pc"},
    "bin": {"x64"},
    "r6": {"scripts", "tweaks", "input", "config"},
    "red4ext": None,
    "engine": {"config", "tools"},
}
# REDmod : "mods/<NomDuMod>/..." — ambigu (un zip peut contenir un dossier "mods"
# qui enveloppe tout), donc utilisé seulement si aucune racine stricte n'est trouvée.
_REDMOD_ROOT = "mods"

# Orthographe canonique des dossiers du jeu. Sous Linux le système de fichiers
# est sensible à la casse : "Archive/PC/Mod" créerait un second dossier.
_CANONICAL_PREFIXES = [
    "bin/x64/plugins/cyber_engine_tweaks/mods",
    "bin/x64/plugins",
    "bin/x64",
    "archive/pc/mod",
    "archive/pc/patch",
    "r6/scripts",
    "r6/tweaks",
    "r6/input",
    "r6/config",
    "red4ext/plugins",
    "engine/config",
    "engine/tools",
    "red4ext",
    "mods",
]

_CET_MODS = "bin/x64/plugins/cyber_engine_tweaks/mods/"


class Cyberpunk2077(Game):
    id = "cyberpunk2077"
    name = "Cyberpunk 2077"
    nexus_domain = "cyberpunk2077"
    steam_app_id = 1091500
    install_dir_name = "Cyberpunk 2077"
    exe_rel_path = "bin/x64/Cyberpunk2077.exe"

    target_dirs = (
        "archive/pc/mod",
        "bin/x64",
        "r6/scripts",
        "r6/tweaks",
        "red4ext/plugins",
        "engine",
        "mods",
    )
    required_frameworks = {
        "cet": Framework("cet", "Cyber Engine Tweaks", "https://www.nexusmods.com/cyberpunk2077/mods/107"),
        "red4ext": Framework("red4ext", "RED4ext", "https://www.nexusmods.com/cyberpunk2077/mods/2380"),
        "redscript": Framework("redscript", "redscript", "https://www.nexusmods.com/cyberpunk2077/mods/1511"),
        "archivexl": Framework("archivexl", "ArchiveXL", "https://www.nexusmods.com/cyberpunk2077/mods/4198"),
        "tweakxl": Framework("tweakxl", "TweakXL", "https://www.nexusmods.com/cyberpunk2077/mods/4197"),
        "codeware": Framework("codeware", "Codeware", "https://www.nexusmods.com/cyberpunk2077/mods/7780"),
    }
    log_paths = (
        "r6/cache",
        "r6/logs",
        "red4ext/logs",
        "bin/x64/plugins/cyber_engine_tweaks",
    )

    # ------------------------------------------------------------------ install

    def is_valid_install(self, path: Path) -> bool:
        return (path / self.exe_rel_path).is_file()

    def detect_install_path(self) -> Path | None:
        for candidate in self._candidate_install_paths():
            if self.is_valid_install(candidate):
                return candidate
        return None

    def _candidate_install_paths(self) -> list[Path]:
        candidates: list[Path] = []
        for library in _steam_libraries():
            candidates.append(library / "steamapps" / "common" / self.install_dir_name)

        home = Path.home()
        if sys.platform == "win32":
            for drive in ("C:", "D:", "E:"):
                candidates += [
                    Path(drive + "/GOG Games") / self.install_dir_name,
                    Path(drive + "/Program Files (x86)/GOG Galaxy/Games") / self.install_dir_name,
                    Path(drive + "/Program Files/Epic Games/Cyberpunk2077"),
                ]
        else:
            # Heroic / Lutris (GOG, Epic)
            candidates += [
                home / "Games" / "Heroic" / self.install_dir_name,
                home / "Games" / self.install_dir_name,
                home / "Games" / "cyberpunk-2077" / "drive_c" / "GOG Games" / self.install_dir_name,
            ]
        return candidates

    # ------------------------------------------------------------------ mapping

    def map_files(self, rel_paths: list[PurePosixPath], mod_id: str) -> MappingResult:
        files: list[FileMapping] = []
        ignored: list[str] = []

        for rel in rel_paths:
            dst = self._map_one(rel, mod_id)
            if dst is None:
                ignored.append(rel.as_posix())
            else:
                files.append(FileMapping(rel.as_posix(), _canonicalize(dst)))

        if not files:
            raise LayoutError(
                "Aucun fichier installable trouvé (ni archive/, bin/, r6/, red4ext/, "
                "ni fichier .archive / .reds isolé)."
            )
        return MappingResult(files, ignored)

    def _map_one(self, rel: PurePosixPath, mod_id: str) -> str | None:
        parts = rel.parts
        lowered = [p.lower() for p in parts]

        # 1. Racine du jeu reconnue quelque part dans le chemin.
        for i, part in enumerate(lowered[:-1]):
            expected = _ROOTS.get(part, "absent")
            if expected == "absent":
                continue
            if expected is None or lowered[i + 1] in expected:
                return "/".join(parts[i:])

        # 2. REDmod : mods/<nom>/... (au moins un dossier sous mods/)
        for i, part in enumerate(lowered[:-2]):
            if part == _REDMOD_ROOT:
                return "/".join(parts[i:])

        # 3. Fichiers isolés, rangés selon leur extension.
        suffix = rel.suffix.lower()
        if suffix in (".archive", ".xl"):
            return f"archive/pc/mod/{rel.name}"
        if suffix == ".reds":
            return f"r6/scripts/{mod_id}/{rel.name}"
        if suffix in (".yaml", ".yml", ".tweak"):
            return f"r6/tweaks/{mod_id}/{rel.name}"
        return None

    # ------------------------------------------------------------- dépendances

    def detect_provides(self, dsts: list[str]) -> list[str]:
        low = {d.lower() for d in dsts}
        provides = []
        if "bin/x64/plugins/cyber_engine_tweaks.asi" in low:
            provides.append("cet")
        if "red4ext/red4ext.dll" in low:
            provides.append("red4ext")
        if "engine/tools/scc.exe" in low or any(d.startswith("engine/tools/scc") for d in low):
            provides.append("redscript")
        for fw in ("archivexl", "tweakxl", "codeware"):
            if any(re.fullmatch(rf"red4ext/plugins/{fw}/{fw}\.dll", d) for d in low):
                provides.append(fw)
        return provides

    def detect_requirements(self, dsts: list[str]) -> list[str]:
        low = [d.lower() for d in dsts]
        req: list[str] = []

        def need(fw: str, cond: bool) -> None:
            if cond and fw not in req:
                req.append(fw)

        need("cet", any(d.startswith(_CET_MODS) for d in low))
        need("redscript", any(d.endswith(".reds") for d in low))
        need("red4ext", any(d.startswith("red4ext/plugins/") for d in low))
        need("archivexl", any(d.endswith(".xl") for d in low))
        need("tweakxl", any(d.startswith("r6/tweaks/") for d in low))
        # ArchiveXL / TweakXL / Codeware sont eux-mêmes des plugins RED4ext.
        provided = set(self.detect_provides(dsts))
        if provided & {"archivexl", "tweakxl", "codeware"}:
            need("red4ext", True)
        return [r for r in req if r not in provided]

    def auto_labels(self, dsts: list[str]) -> list[str]:
        low = [d.lower() for d in dsts]
        labels = []
        if any(d.startswith("archive/") for d in low):
            labels.append("visuel")
        if any(d.startswith("r6/scripts/") for d in low):
            labels.append("script")
        if any(d.startswith(_CET_MODS) for d in low):
            labels.append("cet")
        if any(d.startswith("r6/tweaks/") for d in low):
            labels.append("tweak")
        if any(d.startswith("mods/") for d in low):
            labels.append("redmod")
        if self.detect_provides(dsts):
            labels.append("framework")
        return labels


def _canonicalize(dst: str) -> str:
    low = dst.lower()
    for prefix in _CANONICAL_PREFIXES:
        if low == prefix or low.startswith(prefix + "/"):
            return prefix + dst[len(prefix):]
    return dst


def _steam_libraries() -> list[Path]:
    """Racines des bibliothèques Steam (principale + celles de libraryfolders.vdf)."""
    home = Path.home()
    if sys.platform == "win32":
        pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        roots = [pf86 / "Steam"]
    else:
        roots = [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        ]

    libraries: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        libraries.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            libraries.append(Path(match.group(1).replace("\\\\", "\\")))

    seen: set[Path] = set()
    unique = []
    for lib in libraries:
        key = lib.resolve() if lib.exists() else lib
        if key not in seen:
            seen.add(key)
            unique.append(lib)
    return unique
