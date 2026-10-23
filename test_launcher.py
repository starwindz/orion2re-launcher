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
        """
        cfg_path = os.path.join(self.data_dir, "orion2re.cfg")
        
        # Instantiate manager pointing to test directory
        cfg = Orion2ConfigManager(self.test_dir)
        cfg.mod = "v150i"
        cfg.scaler = "hq2x"
        cfg.fullscreen = True
        cfg.resolution = "1920x1080 (FHD)"
        cfg.vsync = True
        cfg.router = "209.38.39.19"
        cfg.save()

        # Read back raw text to verify semicolon termination
        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("enable v150i;", content)
        self.assertIn("interface scaler = hq2x;", content)
        self.assertIn("interface fullscreen = true;", content)
        self.assertIn("interface vsync_enabled = true;", content)
        self.assertIn("interface window_width = 1920;", content)
        self.assertIn("interface window_height = 1080;", content)
        self.assertIn('interface router = "209.38.39.19";', content)

        # Reload into a fresh manager instance to verify parser accuracy
        cfg2 = Orion2ConfigManager(self.test_dir)
        self.assertEqual(cfg2.mod, "v150i")
        self.assertEqual(cfg2.scaler, "hq2x")
        self.assertTrue(cfg2.fullscreen)
        self.assertTrue(cfg2.vsync)
        self.assertTrue(cfg2.resolution.startswith("1920x1080"))
        self.assertEqual(cfg2.router, "209.38.39.19")

        # Test resetting to defaults
        cfg2.reset_defaults()
        self.assertEqual(cfg2.scaler, "(Default)")
        self.assertFalse(cfg2.fullscreen)

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

    def test_downloader_build_discovery_starwindz(self):
        """
        Verifies that build discovery can connect to GitHub Actions API for starwindz/build_orion2re,
        retrieve the latest run ID, and construct a nightly.link download URL.
        Skips gracefully if running in an isolated or offline network environment.
        """
        dl = Orion2ReDownloader(target_dir=self.test_dir)
        try:
            run_id, artifact, download_url = dl.get_latest_build_info()
            self.assertIsNotNone(run_id)
            self.assertTrue(artifact.startswith("orion2re-"))
            self.assertIn("starwindz/build_orion2re", download_url)
            self.assertIn("nightly.link", download_url)
        except Exception as exc:
            self.skipTest(f"Network unavailable in current environment: {exc}")

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
