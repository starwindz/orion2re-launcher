import os
import re
import sys

def get_app_root():
    """
    Determines the absolute root directory of the application.
    
    When bundled into a standalone executable using PyInstaller, sys.frozen is True
    and sys.executable provides the path to the executable file on the user's filesystem.
    When running from Python source scripts, __file__ provides the script location.
    
    Returns:
        str: Absolute filesystem path to the project root directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))


class Orion2ConfigManager:
    """
    Manages loading, parsing, modifying, and serializing the engine configuration file
    at 'data/orion2re.cfg', as well as scanning available mods in 'data/mods/'.
    
    The syntax strictly complies with Orion2Re's lexical parser (cfg_parser.cpp):
    - All statements must terminate with a semicolon ';'.
    - Key-value assignments use the format: interface <key> = <value>;
    - Mod activation uses the format: enable <mod_name>;
    """

    # Available graphic scaling algorithms supported by the engine and exposed to the user
    SCALER_OPTIONS = ["(Default)", "hq2x"]

    # Supported display window presentation modes
    DISPLAY_MODES = ["Windowed", "Fullscreen"]

    # Common resolution presets for windowed / upscaled rendering
    # Internal native rendering remains 640x480, while the window surface scales accordingly
    RESOLUTION_PRESETS = [
        "640x480 (1x Default)",
        "960x720 (1.5x)",
        "1120x840 (1.75x)",
        "1280x960 (2x)",
        "1440x1080 (2.25x)",
        "1600x1200 (2.5x)",
        "1920x1080 (FHD)",
        "1920x1200 (WUXGA)",
        "2560x1440 (QHD)"
    ]

    ENGINE_OPTIONS = ["Official", "Nightly"]

    # Official community router IP maintained for online multiplayer matching
    DEFAULT_ROUTER = "209.38.39.19"

    def __init__(self, root_dir=None):
        """
        Initializes the configuration manager with a given or auto-detected root directory.

        Args:
            root_dir (str, optional): Target root directory. Defaults to get_app_root().
        """
        self.root_dir = os.path.abspath(root_dir) if root_dir else get_app_root()
        self.data_dir = os.path.join(self.root_dir, "data")
        self.cfg_path = os.path.join(self.data_dir, "orion2re.cfg")
        self.mods_dir = os.path.join(self.data_dir, "mods")

        # Default configuration state
        self.engine = "Official"
        self.mods = ["v150i"]
        self.scaler = "(Default)"
        self.vsync = False
        self.fullscreen = False
        self.resolution = "1280x960 (2x)"
        self.router = self.DEFAULT_ROUTER
        
        # Preserves any arbitrary or advanced config directives not managed directly by the GUI
        self.extra_lines = []

        # Load existing configuration file if present on disk
        self.load()

    @property
    def mod(self):
        """
        Returns the primary (highest-priority) mod for backward compatibility.
        """
        return self.mods[0] if self.mods else "None (Vanilla)"

    @mod.setter
    def mod(self, val):
        """
        Sets a single active mod for backward compatibility.
        """
        if not val or val == "None (Vanilla)":
            self.mods = []
        else:
            self.mods = [val]

    def reset_defaults(self):
        """
        Resets all configuration values to their recommended defaults and writes to disk.
        
        Returns:
            bool: True if configuration was successfully written, False otherwise.
        """
        self.engine = "Official"
        self.mods = ["v150i"]
        self.scaler = "(Default)"
        self.vsync = False
        self.fullscreen = False
        self.resolution = "1280x960 (2x)"
        self.router = self.DEFAULT_ROUTER
        self.extra_lines = []
        return self.save()

    def get_available_mods(self):
        """
        Scans 'data/mods' and the active engine's mod directory for installed mods (.zip archives or subdirectories).
        
        Returns:
            list[str]: Unique list of available mod names including built-in fallbacks.
        """
        mods = ["None (Vanilla)", "v150i", "v150", "v120"]
        engine_key = "official" if getattr(self, "engine", "Official").lower() == "official" else "nightly"
        engine_mods_dir = os.path.join(self.root_dir, "orion2re", engine_key, "data", "mods")

        for target_dir in (self.mods_dir, engine_mods_dir):
            if os.path.isdir(target_dir):
                try:
                    for entry in sorted(os.listdir(target_dir)):
                        name, ext = os.path.splitext(entry)
                        entry_path = os.path.join(target_dir, entry)
                        # A mod can be packaged as a zip file or an unpacked folder
                        if ext.lower() == ".zip" or os.path.isdir(entry_path):
                            if name not in mods:
                                mods.append(name)
                except Exception as e:
                    print(f"[ConfigManager] Error scanning mods in {target_dir}: {e}")
        return mods

    def load(self):
        """
        Reads and parses existing configuration statements from 'data/orion2re.cfg'.
        Recognized options are stored in instance variables; unrecognized lines are
        preserved in self.extra_lines so user customizations remain intact.
        
        Supports reading fullscreen and resolution statements even when commented with '#'.
        
        Returns:
            bool: True if the file was loaded successfully, False if missing or unreadable.
        """
        if not os.path.isfile(self.cfg_path):
            return False

        width = None
        height = None
        self.extra_lines = []
        loaded_mods = []
        engine_explicit = False

        try:
            with open(self.cfg_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            for line in lines:
                stripped = line.strip()

                # 0. Engine selection directive if present: '# launcher_engine = Official|Nightly;'
                m_eng = re.match(r"^#*\s*(?:launcher_engine\s*=\s*|Engine:\s*)(Official|Nightly)", stripped, re.I)
                if m_eng:
                    self.engine = m_eng.group(1).capitalize()
                    engine_explicit = True
                    continue

                # 1. Active mod declaration: 'enable <mod_identifier>;'
                # Multiple mods can be enabled. Order in file defines load priority (top = highest).
                m_mod = re.match(r"^enable\s+([a-zA-Z0-9_\-]+)\s*;", stripped)
                if m_mod:
                    mod_name = m_mod.group(1)
                    if mod_name not in loaded_mods:
                        loaded_mods.append(mod_name)
                    continue

                # 2. Display scaler filter: 'interface scaler = <scaler_type>;'
                m_scaler = re.match(r"^interface\s+scaler\s*=\s*([a-zA-Z0-9_]+)\s*;", stripped)
                if m_scaler:
                    val = m_scaler.group(1)
                    self.scaler = val if val in self.SCALER_OPTIONS else "(Default)"
                    continue

                # 3. Vertical synchronization: 'interface vsync_enabled = true|false;'
                m_vsync = re.match(r"^interface\s+vsync_enabled\s*=\s*(true|false)\s*;", stripped, re.I)
                if m_vsync:
                    self.vsync = (m_vsync.group(1).lower() == "true")
                    continue

                # 4. Fullscreen presentation: 'interface fullscreen = true|false;' (supports commented '#')
                m_fs = re.match(r"^#*\s*interface\s+fullscreen\s*=\s*(true|false)\s*;", stripped, re.I)
                if m_fs:
                    self.fullscreen = (m_fs.group(1).lower() == "true")
                    if not engine_explicit:
                        self.engine = "Official" if stripped.startswith("#") else "Nightly"
                    continue

                # 5. Window width: 'interface window_width = <pixels>;' (supports commented '#')
                m_w = re.match(r"^#*\s*interface\s+window_width\s*=\s*(\d+)\s*;", stripped)
                if m_w:
                    width = int(m_w.group(1))
                    if not engine_explicit and not stripped.startswith("#"):
                        self.engine = "Nightly"
                    continue

                # 6. Window height: 'interface window_height = <pixels>;' (supports commented '#')
                m_h = re.match(r"^#*\s*interface\s+window_height\s*=\s*(\d+)\s*;", stripped)
                if m_h:
                    height = int(m_h.group(1))
                    if not engine_explicit and not stripped.startswith("#"):
                        self.engine = "Nightly"
                    continue

                # 7. Multiplayer router address: 'interface router = "x.x.x.x";'
                m_rt = re.match(r'^interface\s+router\s*=\s*"?([^";\s]+)"?\s*;', stripped)
                if m_rt:
                    self.router = m_rt.group(1)
                    continue

                # Filter out launcher-generated comments and template headers to prevent duplication
                if (stripped.startswith("# Orion2 Redone") or 
                    stripped.startswith("# ---") or
                    stripped.startswith("# Active mods") or
                    stripped.startswith("# Custom user settings") or
                    stripped.startswith("# Startup display settings") or
                    stripped.startswith("# launcher_engine") or
                    re.match(r"^#*\s*interface\s+(scaler|vsync_enabled|fullscreen|window_width|window_height|router)\b", stripped, re.I) or
                    stripped.startswith("# enable ")):
                    continue

                # Preserve any user custom comments or additional config values
                if stripped:
                    self.extra_lines.append(line)

            # Set loaded mods list (or empty if no enable lines were present)
            if loaded_mods:
                self.mods = loaded_mods
            else:
                self.mods = []

            # Match parsed width and height against resolution presets
            if width and height:
                res_key = f"{width}x{height}"
                matched = False
                for preset in self.RESOLUTION_PRESETS:
                    if preset.startswith(res_key):
                        self.resolution = preset
                        matched = True
                        break
                if not matched:
                    self.resolution = res_key

            return True
        except Exception as e:
            print(f"[ConfigManager] Failed to load configuration from {self.cfg_path}: {e}")
            return False

    def save(self):
        """
        Serializes current settings to 'data/orion2re.cfg' in Orion2Re engine format.
        Ensures directory existence and guarantees semicolon termination for each statement.
        If engine is 'Official', Display Mode and Resolution settings are commented out with '# '.
        
        Returns:
            bool: True on success, False on I/O error.
        """
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            lines = [
                "# Orion2 Redone configuration. Generated by Orion2Re Launcher.\n",
                "# ------------------------------------------------------------\n",
                f"# launcher_engine = {self.engine};\n"
            ]

            # 1. Mod statements in priority order (top line = highest priority)
            active_mods = [m for m in self.mods if m and m != "None (Vanilla)"]
            if active_mods:
                lines.append("# Active mods (listed in priority order, highest priority first):\n")
                for m in active_mods:
                    lines.append(f"enable {m};\n")
            else:
                lines.append("# enable v150i;\n")

            # 2. Scaler statement
            if self.scaler and self.scaler != "(Default)":
                lines.append(f"interface scaler = {self.scaler};\n")
            else:
                lines.append("# interface scaler = hq3x;\n")

            # 3. VSync statement
            lines.append(f"interface vsync_enabled = {'true' if self.vsync else 'false'};\n")

            # 4. Fullscreen and window dimensions (prefixed with '# ' for Official engine)
            prefix = "# " if self.engine == "Official" else ""
            lines.append(f"{prefix}interface fullscreen = {'true' if self.fullscreen else 'false'};\n")
            w, h = 1280, 960
            m_dim = re.match(r"^(\d+)x(\d+)", self.resolution)
            if m_dim:
                w, h = int(m_dim.group(1)), int(m_dim.group(2))
            lines.append(f"{prefix}interface window_width = {w};\n")
            lines.append(f"{prefix}interface window_height = {h};\n")

            # 5. Router IP for online multiplayer
            if self.router:
                lines.append(f'interface router = "{self.router}";\n')

            # 6. Append preserved extra/custom user lines (cleaned to prevent duplicate headers)
            cleaned_extras = []
            for extra in self.extra_lines:
                s = extra.strip()
                if (s and 
                    not s.startswith("# Custom user settings") and
                    not s.startswith("# Active mods") and
                    not s.startswith("# Orion2 Redone") and
                    not s.startswith("# launcher_engine") and
                    not s.startswith("# ---")):
                    cleaned_extras.append(extra if extra.endswith("\n") else extra + "\n")

            if cleaned_extras:
                lines.append("\n# Custom user settings:\n")
                lines.extend(cleaned_extras)

            with open(self.cfg_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True
        except Exception as e:
            print(f"[ConfigManager] Failed to save configuration to {self.cfg_path}: {e}")
            return False
