"""CLI minimale en attendant l'interface web.

    python -m fuse import <zip|7z|dossier> [--name ...] [--label ...]
    python -m fuse list
    python -m fuse show <mod_id>
    python -m fuse detect
    python -m fuse set-path <dossier du jeu>
    python -m fuse enable|disable|remove <mod_id>
    python -m fuse verify

La bibliothèque est dans $FUSE_HOME/library (par défaut ./library).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core.library import Library, ModImportError, parse_nexus_filename, slugify
from .core.linker import Linker, LinkError
from .games import GAMES, LayoutError, get_game


def _library() -> Library:
    return Library(Path(os.environ.get("FUSE_HOME", ".")) / "library")


def _linker(args) -> Linker:
    game = get_game(args.game)
    linker = Linker(_library(), game)
    if not linker.state.game_path:
        found = game.detect_install_path()
        if found:
            linker.set_game_path(found)
            print(f"{game.name} trouvé : {found}")
    return linker


def cmd_import(args) -> int:
    game = get_game(args.game)
    lib = _library()
    linker = Linker(lib, game)
    # Remplacer un mod actif : on le débranche, on réimporte, on le rebranche.
    was_enabled = False
    if args.replace:
        src = Path(args.source)
        mod_id = slugify(args.name or parse_nexus_filename(src.stem if src.is_file() else src.name)["name"])
        if linker.is_enabled(mod_id):
            linker.disable(mod_id)
            was_enabled = True
    try:
        m = lib.import_mod(
            Path(args.source), game, name=args.name, version=args.version,
            labels=args.label, replace=args.replace,
        )
    except (ModImportError, LayoutError) as e:
        print(f"Erreur : {e}", file=sys.stderr)
        if was_enabled:
            linker.enable(mod_id)
        return 1
    if was_enabled:
        linker.enable(m.id)
        print("(mod réactivé avec la nouvelle version)")
    print(f"Importé : {m.name} [{m.id}] v{m.version or '?'}")
    print(f"  {len(m.files)} fichier(s) à lier, {len(m.ignored)} ignoré(s)")
    if m.requires:
        print(f"  Requiert : {', '.join(m.requires)}")
    if m.provides:
        print(f"  Fournit  : {', '.join(m.provides)}")
    if m.labels:
        print(f"  Labels   : {', '.join(m.labels)}")
    for w in m.warnings:
        print(f"  ⚠ {w}")
    return 0


def cmd_list(args) -> int:
    lib = _library()
    linker = Linker(lib, get_game(args.game))
    mods = lib.list_mods(args.game)
    if not mods:
        print("Aucun mod.")
    for m in mods:
        mark = "●" if linker.is_enabled(m.id) else "○"
        labels = f"  [{', '.join(m.labels)}]" if m.labels else ""
        print(f"{mark} {m.id:40} {m.version or '':10} {len(m.files):4} fichiers{labels}")
    return 0


def cmd_set_path(args) -> int:
    Linker(_library(), get_game(args.game), Path(args.path))
    print(f"Dossier du jeu : {Path(args.path).expanduser().resolve()}")
    return 0


def cmd_enable(args) -> int:
    _linker(args).enable(args.mod_id)
    print(f"Activé : {args.mod_id}")
    return 0


def cmd_disable(args) -> int:
    for w in _linker(args).disable(args.mod_id):
        print(f"⚠ {w}")
    print(f"Désactivé : {args.mod_id}")
    return 0


def cmd_remove(args) -> int:
    for w in _linker(args).remove(args.mod_id):
        print(f"⚠ {w}")
    print(f"Supprimé : {args.mod_id}")
    return 0


def cmd_verify(args) -> int:
    problems = _linker(args).verify()
    if not problems:
        print("Tout est propre.")
        return 0
    for p in problems:
        owner = f" [{p.mod}]" if p.mod else ""
        print(f"{p.kind:9} {p.dst}{owner} — {p.detail}")
    return 1


def cmd_show(args) -> int:
    m = _library().get(args.game, args.mod_id)
    print(json.dumps(m.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_detect(args) -> int:
    for game in GAMES.values():
        path = game.detect_install_path()
        print(f"{game.name}: {path or 'non trouvé'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fuse")
    parser.add_argument("--game", default="cyberpunk2077", choices=list(GAMES))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="importer un mod")
    p.add_argument("source")
    p.add_argument("--name")
    p.add_argument("--version")
    p.add_argument("--label", action="append")
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_import)

    sub.add_parser("list", help="lister les mods").set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="afficher le manifeste d'un mod")
    p.add_argument("mod_id")
    p.set_defaults(func=cmd_show)

    sub.add_parser("detect", help="trouver les jeux installés").set_defaults(func=cmd_detect)

    p = sub.add_parser("set-path", help="indiquer le dossier du jeu")
    p.add_argument("path")
    p.set_defaults(func=cmd_set_path)

    for cmd, func, help_ in (
        ("enable", cmd_enable, "brancher un mod dans le jeu"),
        ("disable", cmd_disable, "débrancher un mod"),
        ("remove", cmd_remove, "débrancher et effacer un mod"),
    ):
        p = sub.add_parser(cmd, help=help_)
        p.add_argument("mod_id")
        p.set_defaults(func=func)

    sub.add_parser("verify", help="vérifier les liens dans le dossier du jeu").set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (LinkError, FileNotFoundError) as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
