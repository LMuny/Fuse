import json
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath

from fuse.core.library import Library, ModExistsError, ModImportError, parse_nexus_filename, slugify
from fuse.games import LayoutError, get_game

CP = get_game("cyberpunk2077")


def make_zip(path: Path, entries: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.lib = Library(self.tmp / "library")

    def tearDown(self):
        self._tmp.cleanup()

    def dsts(self, manifest):
        return sorted(f.dst for f in manifest.files)


class TestNexusFilename(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(
            parse_nexus_filename("Fast Travel Anywhere-1234-1-2-1699999999"),
            {"name": "Fast Travel Anywhere", "nexus_id": 1234, "version": "1.2"},
        )

    def test_version_with_letters(self):
        meta = parse_nexus_filename("Better Netrunning-2952-v2-1-0a-1700000000")
        self.assertEqual(meta["nexus_id"], 2952)
        self.assertEqual(meta["version"], "v2.1.0a")

    def test_plain_name(self):
        self.assertEqual(parse_nexus_filename("MyMod")["nexus_id"], None)
        self.assertEqual(parse_nexus_filename("MyMod")["name"], "MyMod")

    def test_slugify(self):
        self.assertEqual(slugify("Équipement   Cool! v2"), "equipement-cool-v2")


class TestImport(Base):
    def test_wrapped_archive_and_readme(self):
        z = make_zip(self.tmp / "Fast Travel Anywhere-1234-1-2-1699999999.zip", {
            "FastTravel/archive/pc/mod/ft.archive": b"x",
            "FastTravel/readme.txt": "lisez-moi",
            "__MACOSX/FastTravel/._ft.archive": b"junk",
        })
        m = self.lib.import_mod(z, CP)
        self.assertEqual(m.id, "fast-travel-anywhere")
        self.assertEqual(m.version, "1.2")
        self.assertEqual(m.source, "https://www.nexusmods.com/cyberpunk2077/mods/1234")
        self.assertEqual(self.dsts(m), ["archive/pc/mod/ft.archive"])
        self.assertEqual(m.ignored, ["FastTravel/readme.txt"])
        self.assertIn("visuel", m.labels)

        mod_dir = self.lib.mod_dir("cyberpunk2077", m.id)
        self.assertTrue((mod_dir / "files/FastTravel/archive/pc/mod/ft.archive").is_file())
        self.assertFalse((mod_dir / "files/__MACOSX").exists())
        on_disk = json.loads((mod_dir / "manifest.json").read_text())
        self.assertEqual(on_disk["files"][0]["src"], "FastTravel/archive/pc/mod/ft.archive")
        self.assertEqual([x.id for x in self.lib.list_mods("cyberpunk2077")], [m.id])
        self.assertFalse(any((self.lib.root / ".tmp").iterdir()))

    def test_case_is_canonicalized(self):
        z = make_zip(self.tmp / "Caps.zip", {"Archive/PC/Mod/a.archive": b"x", "R6/Scripts/caps/a.reds": "x"})
        m = self.lib.import_mod(z, CP)
        self.assertEqual(self.dsts(m), ["archive/pc/mod/a.archive", "r6/scripts/caps/a.reds"])

    def test_loose_files(self):
        z = make_zip(self.tmp / "Loose.zip", {"a.archive": b"x", "b.archive.xl": "x", "s.reds": "x"})
        m = self.lib.import_mod(z, CP)
        self.assertEqual(
            self.dsts(m),
            ["archive/pc/mod/a.archive", "archive/pc/mod/b.archive.xl", "r6/scripts/loose/s.reds"],
        )
        self.assertEqual(sorted(m.requires), ["archivexl", "redscript"])

    def test_cet_mod_requirements(self):
        z = make_zip(self.tmp / "CetMod.zip", {
            "bin/x64/plugins/cyber_engine_tweaks/mods/cetmod/init.lua": "-- lua",
            "r6/tweaks/cetmod/x.yaml": "a: 1",
        })
        m = self.lib.import_mod(z, CP)
        self.assertEqual(m.requires, ["cet", "tweakxl"])
        self.assertIn("cet", m.labels)

    def test_framework_provides_itself(self):
        z = make_zip(self.tmp / "ArchiveXL-4198-1-5-0-1700000000.zip", {
            "red4ext/plugins/ArchiveXL/ArchiveXL.dll": b"dll",
            "r6/scripts/ArchiveXL/ArchiveXL.reds": "x",
        })
        m = self.lib.import_mod(z, CP)
        self.assertEqual(m.provides, ["archivexl"])
        self.assertEqual(m.requires, ["redscript", "red4ext"])
        self.assertIn("framework", m.labels)

    def test_redmod(self):
        z = make_zip(self.tmp / "Red.zip", {"mods/MyRedMod/info.json": "{}", "mods/MyRedMod/archives/a.archive": b"x"})
        m = self.lib.import_mod(z, CP)
        self.assertEqual(self.dsts(m), ["mods/MyRedMod/archives/a.archive", "mods/MyRedMod/info.json"])

    def test_wrapper_named_mods_is_not_redmod(self):
        z = make_zip(self.tmp / "Wrap.zip", {"mods/Thing/archive/pc/mod/a.archive": b"x"})
        self.assertEqual(self.dsts(self.lib.import_mod(z, CP)), ["archive/pc/mod/a.archive"])

    def test_folder_import(self):
        src = self.tmp / "My Folder Mod"
        (src / "r6/scripts/m").mkdir(parents=True)
        (src / "r6/scripts/m/m.reds").write_text("x")
        m = self.lib.import_mod(src, CP, labels=["gameplay"])
        self.assertEqual(m.id, "my-folder-mod")
        self.assertEqual(m.labels, ["gameplay", "script"])

    def test_nothing_installable(self):
        z = make_zip(self.tmp / "Doc.zip", {"readme.txt": "x"})
        with self.assertRaises(LayoutError):
            self.lib.import_mod(z, CP)
        self.assertFalse((self.lib.root / "cyberpunk2077").exists())

    def test_unknown_code_files_warn(self):
        z = make_zip(self.tmp / "Half.zip", {"a.archive": b"x", "init.lua": "x"})
        m = self.lib.import_mod(z, CP)
        self.assertEqual(len(m.warnings), 1)
        self.assertIn("init.lua", m.warnings[0])

    def test_zip_slip_rejected(self):
        z = make_zip(self.tmp / "Evil.zip", {"../../evil.archive": b"x"})
        with self.assertRaises(ModImportError):
            self.lib.import_mod(z, CP)
        self.assertFalse((self.tmp / "evil.archive").exists())

    def test_fomod_variants_rejected(self):
        z = make_zip(self.tmp / "Fomod.zip", {
            "fomod/ModuleConfig.xml": "<x/>",
            "Red/archive/pc/mod/car.archive": b"r",
            "Blue/archive/pc/mod/car.archive": b"b",
        })
        with self.assertRaisesRegex(ModImportError, "FOMOD"):
            self.lib.import_mod(z, CP)

    def test_duplicate_and_replace(self):
        z = make_zip(self.tmp / "Dup.zip", {"a.archive": b"1"})
        self.lib.import_mod(z, CP)
        with self.assertRaises(ModExistsError):
            self.lib.import_mod(z, CP)
        make_zip(z, {"b.archive": b"2"})
        m = self.lib.import_mod(z, CP, replace=True)
        self.assertEqual(self.dsts(m), ["archive/pc/mod/b.archive"])
        files = self.lib.files_dir("cyberpunk2077", "dup")
        self.assertEqual([p.name for p in files.iterdir()], ["b.archive"])

    def test_unsupported_format(self):
        f = self.tmp / "mod.txt"
        f.write_text("x")
        with self.assertRaises(ModImportError):
            self.lib.import_mod(f, CP)


class TestMapping(unittest.TestCase):
    def test_bin_root(self):
        r = CP.map_files([PurePosixPath("X/bin/x64/plugins/cyber_engine_tweaks.asi")], "x")
        self.assertEqual(r.files[0].dst, "bin/x64/plugins/cyber_engine_tweaks.asi")
        self.assertEqual(CP.detect_provides([r.files[0].dst]), ["cet"])


if __name__ == "__main__":
    unittest.main()
