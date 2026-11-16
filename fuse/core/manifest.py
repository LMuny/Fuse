"""Lecture / écriture des manifestes de mods (`manifest.json`).

Le manifeste est la source de vérité d'un mod : la base SQLite pourra
toujours être reconstruite en relisant tous les manifestes de la bibliothèque.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1


@dataclass
class ManifestFile:
    src: str  # relatif à <mod>/files/
    dst: str  # relatif au dossier du jeu


@dataclass
class Manifest:
    id: str
    game: str
    name: str
    version: str | None = None
    source: str | None = None
    nexus_id: int | None = None
    original_filename: str | None = None
    installed_at: str | None = None
    requires: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    files: list[ManifestFile] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Manifest:
        data = dict(data)
        data["files"] = [ManifestFile(**f) for f in data.get("files", [])]
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


def write_manifest(mod_dir: Path, manifest: Manifest) -> Path:
    """Écrit le manifeste de façon atomique (fichier temporaire + rename)."""
    path = mod_dir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_manifest(mod_dir: Path) -> Manifest:
    data = json.loads((mod_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    return Manifest.from_dict(data)
