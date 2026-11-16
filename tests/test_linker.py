import json
import os
import tempfile
import unittest
from pathlib import Path

from fuse.core.library import Library
from fuse.core.linker import ConflictError, Linker, LinkError
from fuse.games import get_game
from tests.test_library import make_zip

CP = get_game("cyberpunk2077")


class LinkerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.game = self.tmp / "Cyberpunk 2077"
        (self.game / "bin/x64").mkdir(parents=True)
        (self.game / "bin/x64/Cyberpunk2077.exe").write_bytes(b"exe")
        (self.game / "archive/pc/content").mkdir(parents=True)
        self.lib = Library(self.tmp / "library")
        self.linker = Linker(self.lib, CP, self.game)

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, name, entries):
        return self.lib.import_mod(make_zip(self.tmp / f"{name}.zip", entries), CP)

    def snapshot(self):
        """Tous les chemins du dossier du jeu (pour vérifier qu'il revient à l'identique)."""
        return sorted(p.relative_to(self.game).as_posix() for p in self.game.rglob("*"))

    def test_enable_disable_leaves_game_untouched(self):
        before = self.snapshot()
        self.add("A", {"r6/scripts/a/deep/a.reds": "x", "archive/pc/mod/a.archive": b"x"})
        self.linker.enable("a")

        link = self.game / "r6/scripts/a/deep/a.reds"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_text(), "x")
        self.assertTrue(self.linker.is_enabled("a"))

        self.linker.disable("a")
        self.assertEqual(self.snapshot(), before)  # r6/, archive/pc/mod/ créés par Fuse -> supprimés
        self.assertFalse(self.linker.is_enabled("a"))

    def test_state_persists(self):
        self.add("A", {"a.archive": b"x"})
        self.linker.enable("a")
        again = Linker(self.lib, CP)
        self.assertTrue(again.is_enabled("a"))
        self.assertEqual(again.game_path, self.game.resolve())

    def test_existing_dir_not_removed(self):
        (self.game / "archive/pc/mod").mkdir()
        (self.game / "archive/pc/mod/manual.archive").write_bytes(b"m")
        self.add("A", {"a.archive": b"x"})
        self.linker.enable("a")
        self.linker.disable("a")
        self.assertTrue((self.game / "archive/pc/mod/manual.archive").is_file())

    def test_conflict_refused_and_nothing_linked(self):
        self.add("A", {"shared.archive": b"a"})
        self.add("B", {"shared.archive": b"b", "only_b.archive": b"b"})
        self.linker.enable("a")
        with self.assertRaises(ConflictError) as ctx:
            self.linker.enable("b")
        self.assertEqual(ctx.exception.conflicts, {"archive/pc/mod/shared.archive": "a"})
        self.assertFalse((self.game / "archive/pc/mod/only_b.archive").exists())
        self.assertFalse(self.linker.is_enabled("b"))

    def test_replaced_game_file_is_backed_up_and_restored(self):
        original = self.game / "bin/x64/version.dll"
        original.write_bytes(b"vanilla")
        self.add("Hook", {"bin/x64/version.dll": b"modded"})
        self.linker.enable("hook")
        self.assertEqual(original.read_bytes(), b"modded")
        self.linker.disable("hook")
        self.assertFalse(original.is_symlink())
        self.assertEqual(original.read_bytes(), b"vanilla")
        self.assertFalse((self.lib.root / "cyberpunk2077/.backup").exists())

    def test_occupied_restore_warns_instead_of_overwriting(self):
        original = self.game / "bin/x64/version.dll"
        original.write_bytes(b"vanilla")
        self.add("Hook", {"bin/x64/version.dll": b"modded"})
        self.linker.enable("hook")
        original.unlink()
        original.write_bytes(b"game update")  # le jeu a réécrit le fichier entre-temps
        warnings = self.linker.disable("hook")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(original.read_bytes(), b"game update")
        self.assertTrue((self.lib.root / "cyberpunk2077/.backup/bin/x64/version.dll").is_file())

    def test_rollback_on_failure(self):
        before = self.snapshot()
        m = self.add("A", {"a.archive": b"x", "b.archive": b"y"})
        # Fichier source supprimé de la bibliothèque -> échec avant de toucher au jeu.
        (self.lib.files_dir("cyberpunk2077", "a") / "b.archive").unlink()
        with self.assertRaises(LinkError):
            self.linker.enable("a")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.linker.state.links, {})

    def test_remove(self):
        self.add("A", {"a.archive": b"x"})
        self.linker.enable("a")
        self.linker.remove("a")
        self.assertFalse(self.lib.mod_dir("cyberpunk2077", "a").exists())
        self.assertFalse((self.game / "archive/pc/mod").exists())
        self.assertEqual(self.lib.list_mods("cyberpunk2077"), [])

    def test_verify(self):
        self.add("A", {"a.archive": b"x", "b.archive": b"y"})
        self.linker.enable("a")
        self.assertEqual(self.linker.verify(), [])

        (self.game / "archive/pc/mod/a.archive").unlink()
        stray = self.game / "archive/pc/mod/stray.archive"
        stray.symlink_to(self.lib.files_dir("cyberpunk2077", "a") / "b.archive")
        kinds = {(p.kind, p.dst) for p in self.linker.verify()}
        self.assertEqual(kinds, {("missing", "archive/pc/mod/a.archive"), ("orphan", "archive/pc/mod/stray.archive")})

    def test_disable_does_not_delete_foreign_file(self):
        self.add("A", {"a.archive": b"x"})
        self.linker.enable("a")
        target = self.game / "archive/pc/mod/a.archive"
        target.unlink()
        target.write_bytes(b"installed by hand")
        self.linker.disable("a")
        self.assertEqual(target.read_bytes(), b"installed by hand")

    def test_change_game_path_refused_while_active(self):
        self.add("A", {"a.archive": b"x"})
        self.linker.enable("a")
        other = self.tmp / "Other"
        (other / "bin/x64").mkdir(parents=True)
        (other / "bin/x64/Cyberpunk2077.exe").write_bytes(b"exe")
        with self.assertRaises(LinkError):
            self.linker.set_game_path(other)

    def test_invalid_game_path(self):
        with self.assertRaises(LinkError):
            Linker(self.lib, CP, self.tmp)


if __name__ == "__main__":
    unittest.main()
