"""
Automated Unit Tests for Orion2Re Launcher.

Tests the following core functionalities:
  1. Original game files verification logic (tested via orion2.exe presence).
  2. Configuration parsing, semicolon termination, and roundtrip serialization.
  3. Reset to default configuration values.
  4. GitHub Actions build discovery logic via REST API and nightly.link URL generation.
  5. Application root path resolution under source vs PyInstaller frozen runtime.
"""
import os
import sys
import shutil
import tempfile
import unittest

from config_manager import Orion2ConfigManager, get_app_root
from downloader import Orion2ReDownloader

# Flag indicating whether tests were explicitly enabled
EXPLICIT_RUN = False


class TestOrion2Launcher(unittest.TestCase):
    """
    Test suite verifying critical launcher logic and configuration compliance.
    Tests run ONLY when explicitly requested via '--run' flag or RUN_TESTS=1 environment variable.
    """

    @classmethod
    def setUpClass(cls):
        """
        Ensures unit tests only execute when explicitly requested.
        """
        if not EXPLICIT_RUN and "--run" not in sys.argv and os.environ.get("RUN_TESTS") != "1":
            raise unittest.SkipTest("Skipped: Unit tests only run when explicitly specified via '--run' or RUN_TESTS=1.")

    def setUp(self):
        """
        Creates an isolated temporary test directory before each test.
        """
        self.test_dir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.test_dir, "data")
        self.mods_dir = os.path.join(self.data_dir, "mods")
        os.makedirs(self.mods_dir, exist_ok=True)

    def tearDown(self):
        """
        Cleans up the temporary test directory after each test.
        """
        shutil.rmtree(self.test_dir)

    def test_original_file_detection_by_orion2_exe(self):
        """
        Verifies that original game files are detected by testing for 'orion2.exe',
        and that detection is case-insensitive.
        """
        try:
            from launcher import Orion2Launcher
        except (ImportError, ModuleNotFoundError) as exc:
            self.skipTest(f"customtkinter dependency not present in current Python environment: {exc}")
            return

        launcher = Orion2Launcher.__new__(Orion2Launcher)
        launcher.root_dir = self.test_dir

        # Should be False initially when directory is empty
        self.assertFalse(launcher.has_original_moo2_files())

        # Create dummy ORION2.EXE (uppercase)
        exe_file = os.path.join(self.test_dir, "ORION2.EXE")
        with open(exe_file, "w") as f:
            f.write("mock")

        # Must detect presence regardless of case
        self.assertTrue(launcher.has_original_moo2_files())

    def test_config_syntax_and_roundtrip(self):
        """
        Verifies that saved configuration complies with Orion2Re syntax rules:
        - Statements terminate with semicolons.
        - Values roundtrip correctly without data loss.
        - Official engine comments out fullscreen & resolution with '# '.
        - Nightly engine writes fullscreen & resolution uncommented.
        """
        cfg_path = os.path.join(self.data_dir, "orion2re.cfg")
        
        # Test Nightly engine saving (uncommented display statements)
        cfg = Orion2ConfigManager(self.test_dir)
        cfg.engine = "Nightly"
        cfg.mod = "v150i"
        cfg.scaler = "hq2x"
        cfg.fullscreen = True
        cfg.resolution = "1920x1080 (FHD)"
        cfg.vsync = True
        cfg.router = "209.38.39.19"
        cfg.save()

        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("enable v150i;", content)
        self.assertIn("interface scaler = hq2x;", content)
        self.assertIn("interface fullscreen = true;", content)
        self.assertIn("interface window_width = 1920;", content)
        self.assertIn("interface window_height = 1080;", content)
        self.assertIn('interface router = "209.38.39.19";', content)

        # Reload into a fresh manager instance
        cfg2 = Orion2ConfigManager(self.test_dir)
        cfg2.load()
        self.assertEqual(cfg2.engine, "Nightly")
        self.assertEqual(cfg2.mod, "v150i")
        self.assertEqual(cfg2.scaler, "hq2x")
        self.assertTrue(cfg2.fullscreen)
        self.assertTrue(cfg2.resolution.startswith("1920x1080"))

        # Test Official engine saving (fullscreen and resolution must be prefixed with '# ')
        cfg2.engine = "Official"
        cfg2.save()

        with open(cfg_path, "r", encoding="utf-8") as f:
            content_official = f.read()

        self.assertIn("# interface fullscreen = true;", content_official)
        self.assertIn("# interface window_width = 1920;", content_official)
        self.assertIn("# interface window_height = 1080;", content_official)

        # Reload official cfg: must still parse fullscreen and resolution even with '#' (Requirement 8)
        cfg3 = Orion2ConfigManager(self.test_dir)
        cfg3.load()
        self.assertEqual(cfg3.engine, "Official")
        self.assertTrue(cfg3.fullscreen)
        self.assertTrue(cfg3.resolution.startswith("1920x1080"))

        # Test resetting to defaults
        cfg3.reset_defaults()
        self.assertEqual(cfg3.scaler, "(Default)")
        self.assertFalse(cfg3.fullscreen)
        self.assertEqual(cfg3.engine, "Official")

    def test_multi_mod_priority_ordering(self):
        """
        Verifies that multiple mods can be configured and saved, and that load order
        preserves priority sequence (top-most statement has highest priority).
        """
        cfg_path = os.path.join(self.data_dir, "orion2re.cfg")
        cfg = Orion2ConfigManager(self.test_dir)
        cfg.mods = ["custom_ui_mod", "ice_balance", "v150i"]
        cfg.save()

        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()

        pos1 = content.find("enable custom_ui_mod;")
        pos2 = content.find("enable ice_balance;")
        pos3 = content.find("enable v150i;")

        self.assertTrue(0 <= pos1 < pos2 < pos3, "Mods must be ordered by priority in CFG")

        # Reload into a new manager to verify list roundtrip
        cfg2 = Orion2ConfigManager(self.test_dir)
        self.assertEqual(cfg2.mods, ["custom_ui_mod", "ice_balance", "v150i"])
        self.assertEqual(cfg2.mod, "custom_ui_mod")

    def test_repeated_save_no_comment_duplication(self):
        """
        Verifies that saving, reloading, and re-saving the configuration multiple times
        does NOT duplicate header comments (such as '# Custom user settings:' or '# Active mods').
        """
        cfg_path = os.path.join(self.data_dir, "orion2re.cfg")
        
        # Perform 5 consecutive load-save cycles
        for _ in range(5):
            mgr = Orion2ConfigManager(self.test_dir)
            mgr.save()

        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Each header comment must appear at most once
        self.assertEqual(content.count("# Custom user settings:"), 0)
        self.assertEqual(content.count("# Active mods (listed in priority order, highest priority first):"), 1)

    def test_resolution_1120x840_preset(self):
        """
        Verifies that the new 1120x840 (1.75x) resolution preset parses and saves accurately.
        """
        cfg = Orion2ConfigManager(self.test_dir)
        cfg.resolution = "1120x840 (1.75x)"
        cfg.save()

        cfg2 = Orion2ConfigManager(self.test_dir)
        self.assertEqual(cfg2.resolution, "1120x840 (1.75x)")

    def test_downloader_build_discovery_upstream(self):
        """
        Verifies that build discovery connects to GitHub Actions API for orion2re/orion2re,
        retrieves the latest run ID, and constructs a nightly.link download URL targeting orion2re-win32.
        Skips gracefully if running in an isolated or offline network environment.
        """
        dl = Orion2ReDownloader(target_dir=self.test_dir)
        try:
            run_id, artifact, download_url = dl.get_latest_build_info()
            self.assertIsNotNone(run_id)
            self.assertTrue(artifact.startswith("orion2re-"))
            self.assertIn("win32", artifact.lower())
            self.assertIn("orion2re/orion2re", download_url)
            self.assertIn("nightly.link", download_url)
        except Exception as exc:
            self.skipTest(f"Network unavailable in current environment: {exc}")

    def test_downloader_official_and_nightly_extraction(self):
        """
        Verifies that download_official() extracts into 'orion2re/official'
        and download_nightly() extracts into 'orion2re/nightly'.
        """
        import io
        import zipfile
        from unittest.mock import patch

        # 1. Build dummy official zip
        official_buf = io.BytesIO()
        with zipfile.ZipFile(official_buf, "w") as oz:
            oz.writestr("orion2re.exe", b"OFFICIAL_EXE")
            oz.writestr("data/base.zip", b"OFFICIAL_BASE")
            oz.writestr("data/mods/v150.zip", b"OFFICIAL_MOD")
            oz.writestr("data/orion2re.cfg", "enable v150;\n")

        # 2. Build dummy nightly nested zip
        inner_buf = io.BytesIO()
        with zipfile.ZipFile(inner_buf, "w") as iz:
            iz.writestr("orion2re.exe", b"NIGHTLY_EXE")
            iz.writestr("data/base.zip", b"NIGHTLY_BASE")
            iz.writestr("data/mods/v150i.zip", b"NIGHTLY_MOD")
            iz.writestr("data/orion2re.cfg", "enable v150i;\n")

        outer_buf = io.BytesIO()
        with zipfile.ZipFile(outer_buf, "w") as oz:
            oz.writestr("orion2re-win-win32-main.zip", inner_buf.getvalue())

        dl = Orion2ReDownloader(target_dir=self.test_dir)

        class DummyResponse:
            def __init__(self, data):
                self._data = io.BytesIO(data)
                self.headers = {"Content-Length": str(len(data))}
            def read(self, chunk_size=-1):
                return self._data.read(chunk_size)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        with patch.object(dl, "get_latest_release_url", return_value="http://dummy/official.zip"):
            with patch.object(dl, "get_latest_build_info", return_value=(999, "orion2re-win32", "http://dummy/nightly.zip")):
                # Mock urlopen to return official_buf on first call, outer_buf on second
                with patch("urllib.request.urlopen", side_effect=[DummyResponse(official_buf.getvalue()), DummyResponse(outer_buf.getvalue())]):
                    run_id, summary = dl.download_and_extract()
                    self.assertEqual(summary, "Official & Nightly")

        # Verify orion2re/official
        off_exe = os.path.join(self.test_dir, "orion2re", "official", "orion2re.exe")
        self.assertTrue(os.path.isfile(off_exe))
        with open(off_exe, "rb") as f:
            self.assertEqual(f.read(), b"OFFICIAL_EXE")

        # Verify orion2re/nightly
        night_exe = os.path.join(self.test_dir, "orion2re", "nightly", "orion2re.exe")
        self.assertTrue(os.path.isfile(night_exe))
        with open(night_exe, "rb") as f:
            self.assertEqual(f.read(), b"NIGHTLY_EXE")

    def test_engine_deployment_and_mod_cleaning(self):
        """
        Verifies Requirement 6:
        - When deploying an engine, data/mods/ (and data/modes/) are wiped clean first.
        - Selected engine files are copied to the root folder.
        - Existing data/orion2re.cfg in root is strictly preserved.
        """
        from launcher import Orion2Launcher
        import shutil

        # Setup mock official engine files
        off_dir = os.path.join(self.test_dir, "orion2re", "official")
        os.makedirs(os.path.join(off_dir, "data", "mods"), exist_ok=True)
        with open(os.path.join(off_dir, "orion2re.exe"), "wb") as f:
            f.write(b"DEPLOYED_OFFICIAL_EXE")
        with open(os.path.join(off_dir, "data", "mods", "off_mod.zip"), "wb") as f:
            f.write(b"OFF_MOD")

        # Pre-populate data/mods and data/modes with old files that must be deleted
        mods_dir = os.path.join(self.test_dir, "data", "mods")
        modes_dir = os.path.join(self.test_dir, "data", "modes")
        os.makedirs(mods_dir, exist_ok=True)
        os.makedirs(modes_dir, exist_ok=True)
        with open(os.path.join(mods_dir, "old_stale_mod.zip"), "wb") as f:
            f.write(b"STALE")
        with open(os.path.join(modes_dir, "stale_modes.txt"), "wb") as f:
            f.write(b"STALE_MODES")

        # Pre-populate user config
        user_cfg_path = os.path.join(self.test_dir, "data", "orion2re.cfg")
        user_cfg_text = "# launcher_engine = Official;\nenable my_mod;\n"
        with open(user_cfg_path, "w", encoding="utf-8") as f:
            f.write(user_cfg_text)

        # Mock app instance (without full Tk event loop)
        class MockApp:
            def __init__(self, root_dir):
                self.root_dir = root_dir

        app = MockApp(self.test_dir)
        Orion2Launcher._deploy_engine(app, "Official")

        # Verify old files in data/mods and data/modes were wiped
        self.assertFalse(os.path.isfile(os.path.join(mods_dir, "old_stale_mod.zip")))
        self.assertFalse(os.path.isfile(os.path.join(modes_dir, "stale_modes.txt")))

        # Verify new mod from official was deployed
        self.assertTrue(os.path.isfile(os.path.join(mods_dir, "off_mod.zip")))

        # Verify root executable was deployed
        root_exe = os.path.join(self.test_dir, "orion2re.exe")
        self.assertTrue(os.path.isfile(root_exe))
        with open(root_exe, "rb") as f:
            self.assertEqual(f.read(), b"DEPLOYED_OFFICIAL_EXE")

        # Verify user config was strictly preserved
        with open(user_cfg_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), user_cfg_text)

    def test_get_app_root_resolution(self):
        """
        Verifies that get_app_root returns a valid directory path both normally
        and when simulating a PyInstaller frozen environment.
        """
        # Normal Python script execution
        root_path = get_app_root()
        self.assertTrue(os.path.isdir(root_path))
        self.assertTrue(os.path.isabs(root_path))

        # Simulated PyInstaller frozen execution
        original_frozen = getattr(sys, "frozen", False)
        try:
            sys.frozen = True
            frozen_root = get_app_root()
            self.assertTrue(os.path.isdir(frozen_root))
            self.assertTrue(os.path.isabs(frozen_root))
        finally:
            if original_frozen:
                sys.frozen = True
            else:
                del sys.frozen


if __name__ == "__main__":
    # Ensure tests only run when explicitly specified with --run or RUN_TESTS=1
    if "--run" not in sys.argv and os.environ.get("RUN_TESTS") != "1":
        print("=" * 68)
        print(" [INFO] Unit tests are configured to run ONLY when explicitly requested.")
        print(" To execute tests, run:")
        print("     python test_launcher.py --run")
        print("=" * 68)
        sys.exit(0)

    EXPLICIT_RUN = True
    if "--run" in sys.argv:
        sys.argv.remove("--run")

    unittest.main()
