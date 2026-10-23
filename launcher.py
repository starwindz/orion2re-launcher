import os
import re
import sys
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox

def get_app_root():
    """
    Determines the real application root directory.
    
    CRITICAL FOR PYINSTALLER STANDALONE EXECUTABLE:
    When bundled using PyInstaller (--onefile), sys.frozen is set to True and
    __file__ points to a temporary extraction directory (_MEIPASS / Temp).
    In this scenario, sys.executable points to the actual directory where
    launcher.exe resides, which is where orion2.exe, orion2re.exe, and data/ are located.
    
    When running directly from Python source scripts, __file__ points to the script path.
    
    Returns:
        str: Absolute filesystem path to the project root directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))

# Ensure customtkinter is installed or provide automatic bootstrap
try:
    import customtkinter as ctk
except ModuleNotFoundError:
    if "unittest" in sys.modules:
        raise
    root = tk.Tk()
    root.withdraw()
    res = messagebox.askyesno(
        "Missing Dependency",
        "Orion2Re Launcher requires the 'customtkinter' library.\n\n"
        "Would you like to install it automatically now?\n(pip install customtkinter)"
    )
    if res:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "customtkinter"])
            import customtkinter as ctk
        except Exception as e:
            messagebox.showerror(
                "Installation Failed",
                f"Failed to install customtkinter: {e}\nPlease run 'pip install -r requirements.txt' manually."
            )
            sys.exit(1)
    else:
        sys.exit(0)

from config_manager import Orion2ConfigManager
from downloader import Orion2ReDownloader

# Deactivate automatic DPI awareness to prevent CustomTkinter from double-scaling
# window dimensions on Windows high-DPI displays (e.g. 125%, 150%), which causes
# the window to exceed screen boundaries and ruins horizontal/vertical centering.
ctk.deactivate_automatic_dpi_awareness()

# Set modern dark visual theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ModOrderDialog(ctk.CTkToplevel):
    """
    Dedicated modal dialog for managing multiple Orion2Re mods and their load priority order.
    
    Orion2Re Package Resolution Rule:
    When enabling multiple mods, the engine loads packages in order where the top-most
    mod has the highest precedence (overriding any assets/configs from lower mods).
    Dependencies (such as v150 required by v150i) are activated automatically by the engine.
    """

    def __init__(self, parent, config_mgr, on_apply):
        super().__init__(parent)
        self.parent = parent
        self.config_mgr = config_mgr
        self.on_apply = on_apply

        # Working copy of active mods list (ordered from highest to lowest priority)
        self.active_mods = list(self.config_mgr.mods)

        self.title("Orion2Re Mod Priority & Multi-Mod Manager")
        self.geometry("520x480")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Center dialog window relative to parent launcher window
        self.update_idletasks()
        try:
            px = parent.winfo_x() + (parent.winfo_width() - 520) // 2
            py = parent.winfo_y() + (parent.winfo_height() - 480) // 2
            self.geometry(f"520x480+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass

        self._build_ui()
        self._refresh_lists()

    def _build_ui(self):
        """
        Builds the dialog layout including priority list, reorder controls, and add mod inputs.
        """
        container = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=0)
        container.pack(fill="both", expand=True, padx=12, pady=12)

        # 1. Header explanation banner
        header = ctk.CTkFrame(container, fg_color="#1e293b", corner_radius=6, border_width=1, border_color="#334155")
        header.pack(fill="x", pady=(0, 10), ipady=4)

        ctk.CTkLabel(
            header,
            text="MOD PRIORITY & MULTI-MOD LOAD ORDER",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#60a5fa"
        ).pack(anchor="w", padx=10, pady=(4, 0))

        ctk.CTkLabel(
            header,
            text="Top-most mod has highest precedence (overrides lower mods).\nDependencies (e.g. v150) are auto-loaded by the Orion2Re engine.",
            font=ctk.CTkFont(size=10),
            text_color="#94a3b8",
            justify="left"
        ).pack(anchor="w", padx=10, pady=(0, 4))

        # 2. Active Mods section header
        ctk.CTkLabel(
            container,
            text="Active Mods (Load Order - Top is Highest Priority):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#e2e8f0"
        ).pack(anchor="w", pady=(0, 4))

        # List frame holding Listbox and action buttons
        list_frame = ctk.CTkFrame(container, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, pady=(0, 10))
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        # tk.Listbox for clear, fast selection and reordering
        self.listbox = tk.Listbox(
            list_frame,
            bg="#1e293b",
            fg="#f8fafc",
            selectbackground="#2563eb",
            selectforeground="#ffffff",
            font=("Segoe UI", 10),
            activestyle="none",
            borderwidth=1,
            relief="solid",
            highlightthickness=0
        )
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # Reordering control buttons column
        order_btn_frame = ctk.CTkFrame(list_frame, fg_color="transparent")
        order_btn_frame.grid(row=0, column=1, sticky="ns")

        self.btn_up = ctk.CTkButton(
            order_btn_frame,
            text="▲ Move Up",
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._move_up
        )
        self.btn_up.pack(pady=(0, 6))

        self.btn_down = ctk.CTkButton(
            order_btn_frame,
            text="▼ Move Down",
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._move_down
        )
        self.btn_down.pack(pady=(0, 6))

        self.btn_remove = ctk.CTkButton(
            order_btn_frame,
            text="✖ Remove",
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            command=self._remove_selected
        )
        self.btn_remove.pack(pady=(0, 6))

        # 3. Add Mod Section (Discovered installed mods & custom identifier entry)
        add_frame = ctk.CTkFrame(container, fg_color="#1e293b", corner_radius=6, border_width=1, border_color="#334155")
        add_frame.pack(fill="x", pady=(0, 10), ipady=4)

        # Row 1: Add discovered installed mods
        row1 = ctk.CTkFrame(add_frame, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(row1, text="Available Mods:", font=ctk.CTkFont(size=11, weight="bold"), width=110, anchor="w").pack(side="left")
        self.avail_combo = ctk.CTkComboBox(row1, values=["(No inactive mods)"], state="readonly", height=24)
        self.avail_combo.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.btn_add = ctk.CTkButton(
            row1,
            text="➕ Add Mod",
            width=90,
            height=24,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._add_selected_avail
        )
        self.btn_add.pack(side="right")

        # Row 2: Add custom mod identifier
        row2 = ctk.CTkFrame(add_frame, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(row2, text="Custom Mod ID:", font=ctk.CTkFont(size=11), width=110, anchor="w").pack(side="left")
        self.custom_entry = ctk.CTkEntry(row2, placeholder_text="e.g. my_custom_mod", height=24, font=ctk.CTkFont(size=11))
        self.custom_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.btn_add_custom = ctk.CTkButton(
            row2,
            text="➕ Add Custom",
            width=90,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#475569",
            hover_color="#64748b",
            command=self._add_custom
        )
        self.btn_add_custom.pack(side="right")

        # 4. Bottom action bar
        bottom_bar = ctk.CTkFrame(container, fg_color="transparent")
        bottom_bar.pack(fill="x", pady=(4, 0))

        self.btn_clear = ctk.CTkButton(
            bottom_bar,
            text="Vanilla (None)",
            width=95,
            height=26,
            font=ctk.CTkFont(size=10),
            fg_color="#334155",
            hover_color="#475569",
            command=self._clear_all
        )
        self.btn_clear.pack(side="left")

        self.btn_default = ctk.CTkButton(
            bottom_bar,
            text="Default (v150i)",
            width=95,
            height=26,
            font=ctk.CTkFont(size=10),
            fg_color="#334155",
            hover_color="#475569",
            command=self._set_default
        )
        self.btn_default.pack(side="left", padx=6)

        self.btn_apply = ctk.CTkButton(
            bottom_bar,
            text="Apply & Close",
            width=110,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=self._apply
        )
        self.btn_apply.pack(side="right")

        self.btn_cancel = ctk.CTkButton(
            bottom_bar,
            text="Cancel",
            width=80,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#475569",
            hover_color="#64748b",
            command=self.destroy
        )
        self.btn_cancel.pack(side="right", padx=(0, 6))

    def _refresh_lists(self, selected_index=None):
        """
        Synchronizes listbox items and inactive mod combobox entries.
        """
        self.listbox.delete(0, "end")
        for i, mod_id in enumerate(self.active_mods):
            if i == 0:
                badge = "[Highest Priority]"
            else:
                badge = f"[Priority #{i + 1}]"
            self.listbox.insert("end", f" {i + 1}. {badge.ljust(18)}  {mod_id}")

        if selected_index is not None and 0 <= selected_index < len(self.active_mods):
            self.listbox.selection_set(selected_index)
            self.listbox.activate(selected_index)

        # Refresh inactive discovered mods
        all_mods = [m for m in self.config_mgr.get_available_mods() if m != "None (Vanilla)"]
        inactive = [m for m in all_mods if m not in self.active_mods]
        if inactive:
            self.avail_combo.configure(values=inactive, state="readonly")
            self.avail_combo.set(inactive[0])
            self.btn_add.configure(state="normal")
        else:
            self.avail_combo.configure(values=["(All installed mods are active)"], state="disabled")
            self.avail_combo.set("(All installed mods are active)")
            self.btn_add.configure(state="disabled")

    def _move_up(self):
        """
        Moves the currently selected mod up by one position in priority.
        """
        sel = self.listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        self.active_mods[idx - 1], self.active_mods[idx] = self.active_mods[idx], self.active_mods[idx - 1]
        self._refresh_lists(selected_index=idx - 1)

    def _move_down(self):
        """
        Moves the currently selected mod down by one position in priority.
        """
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.active_mods) - 1:
            return
        idx = sel[0]
        self.active_mods[idx], self.active_mods[idx + 1] = self.active_mods[idx + 1], self.active_mods[idx]
        self._refresh_lists(selected_index=idx + 1)

    def _remove_selected(self):
        """
        Removes the selected mod from the active mods load list.
        """
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.active_mods[idx]
        new_sel = min(idx, len(self.active_mods) - 1) if self.active_mods else None
        self._refresh_lists(selected_index=new_sel)

    def _add_selected_avail(self):
        """
        Appends the selected inactive mod to the active load list.
        """
        choice = self.avail_combo.get()
        if choice and choice not in self.active_mods and not choice.startswith("("):
            self.active_mods.append(choice)
            self._refresh_lists(selected_index=len(self.active_mods) - 1)

    def _add_custom(self):
        """
        Sanitizes and appends a user-specified custom mod identifier to the load list.
        """
        raw = self.custom_entry.get().strip()
        clean = re.sub(r"[^a-zA-Z0-9_\-]", "", raw)
        if clean:
            if clean not in self.active_mods:
                self.active_mods.append(clean)
                self.custom_entry.delete(0, "end")
                self._refresh_lists(selected_index=len(self.active_mods) - 1)
            else:
                self.custom_entry.delete(0, "end")

    def _clear_all(self):
        """
        Clears all active mods to run pure unmodded vanilla.
        """
        self.active_mods = []
        self._refresh_lists()

    def _set_default(self):
        """
        Resets active mod list to recommended single mod default ('v150i').
        """
        self.active_mods = ["v150i"]
        self._refresh_lists(selected_index=0)

    def _apply(self):
        """
        Applies changes to the configuration manager and invokes parent callback.
        """
        self.config_mgr.mods = list(self.active_mods)
        if self.on_apply:
            self.on_apply()
        self.destroy()


class Orion2Launcher(ctk.CTk):
    """
    Main GUI Window for the Orion2Re Launcher.
    
    Provides:
      - Real-time verification of original Master of Orion II game data (via root executable check).
      - One-click automated download and unpacking of the latest Orion2Re engine build.
      - Visual configuration editor for engine and graphic preferences (data/orion2re.cfg).
      - Synchronous game launch mechanism with auto-save, window minimize, and post-game restore.
    """

    def __init__(self):
        super().__init__()

        self.title("Master of Orion II - Orion2Re Launcher")

        # Resolve real root directory (handles PyInstaller bundle vs Python script)
        self.root_dir = get_app_root()
        self.exe_path = os.path.join(self.root_dir, "orion2re.exe")

        # Center the window on the user's desktop screen with 720x704 geometry (increased 5% from 670px)
        self.minsize(680, 660)
        self._center_on_screen(720, 704)

        # Configuration manager & state tracking
        self.config_mgr = Orion2ConfigManager(self.root_dir)
        self.is_downloading = False
        self.is_game_running = False

        # Construct interface components
        self._build_ui()

        # Perform initial environment status check
        self.refresh_environment_status()

    def _center_on_screen(self, window_w=720, window_h=704):
        """
        Calculates and applies exact horizontal and vertical centering on the user's desktop.
        Accounts for the Windows taskbar (via SPI_GETWORKAREA) to ensure true visual centering
        in the available workspace rather than overflowing beneath the taskbar.
        """
        # Default fallback centering using standard screen metrics
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        pos_x = max(0, (screen_w - window_w) // 2)
        pos_y = max(0, (screen_h - window_h) // 2)

        # On Windows, query the usable desktop working area (excluding taskbar)
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]

                rect = RECT()
                # SPI_GETWORKAREA = 0x0030
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    work_w = rect.right - rect.left
                    work_h = rect.bottom - rect.top
                    pos_x = rect.left + max(0, (work_w - window_w) // 2)
                    pos_y = rect.top + max(0, (work_h - window_h) // 2)
            except Exception as e:
                print(f"[Launcher] Fallback to standard screen dimensions: {e}")

        self.geometry(f"{window_w}x{window_h}+{pos_x}+{pos_y}")

    def _build_ui(self):
        """
        Builds the primary UI structure inside the root window.
        Organized into header, status card, download card, settings card, and bottom play bar.
        """
        # Main content container frame
        self.main_container = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=0)
        self.main_container.pack(fill="both", expand=True, padx=16, pady=(8, 0))

        # 1. Header Banner & Title
        self._build_header(self.main_container)

        # 2. Status Card (Original MOO2 files & Orion2Re executable verification)
        self._build_status_card(self.main_container)

        # 3. Orion2Re Download & Update Card
        self._build_download_card(self.main_container)

        # 4. Settings Card (data/orion2re.cfg configuration controls)
        self._build_settings_card(self.main_container)

        # 5. Bottom Play Action Bar
        self._build_play_section(self)

    def _build_header(self, parent):
        """
        Renders the top branding header panel with title, subtitle, and author credit.
        """
        header_frame = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8, border_width=1, border_color="#334155")
        header_frame.pack(fill="x", pady=(0, 6), ipady=2)

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="MASTER OF ORION II",
            font=ctk.CTkFont(size=19, weight="bold"),
            text_color="#60a5fa"
        )
        title_lbl.pack(pady=(2, 0))

        sub_lbl = ctk.CTkLabel(
            header_frame,
            text="Orion2Re (Master of Orion 2 Redone) Launcher",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="#94a3b8"
        )
        sub_lbl.pack(pady=(0, 0))

        # Bottom row inside header: Documentation link on the left, Author credit on the right
        bottom_row = ctk.CTkFrame(header_frame, fg_color="transparent")
        bottom_row.pack(fill="x", padx=10, pady=(0, 2))

        # Documentation link (opens https://moo2mod.com/redone/doc/index.html)
        doc_url = "https://moo2mod.com/redone/doc/index.html"
        doc_lbl = ctk.CTkLabel(
            bottom_row,
            text="🌐 Documentation",
            font=ctk.CTkFont(size=10, underline=True),
            text_color="#38bdf8",
            cursor="hand2"
        )
        doc_lbl.pack(side="left")
        doc_lbl.bind("<Button-1>", lambda e: webbrowser.open_new_tab(doc_url))
        doc_lbl.bind("<Enter>", lambda e: doc_lbl.configure(text_color="#93c5fd"))
        doc_lbl.bind("<Leave>", lambda e: doc_lbl.configure(text_color="#38bdf8"))

        # Author credit label placed at the bottom-right corner of the top panel
        credit_lbl = ctk.CTkLabel(
            bottom_row,
            text="Launcher created by starwindz",
            font=ctk.CTkFont(size=10),
            text_color="#64748b"
        )
        credit_lbl.pack(side="right")

    def _build_status_card(self, parent):
        """
        Renders the environment status card displaying verification status of
        original MOO2 assets and Orion2Re executable existence.
        """
        self.status_frame = ctk.CTkFrame(parent, corner_radius=8, fg_color="#1e293b", border_width=1, border_color="#334155")
        self.status_frame.pack(fill="x", pady=(0, 6), ipady=3)

        # Top row with section title and Refresh button
        top_row = ctk.CTkFrame(self.status_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(0, 4))

        status_title = ctk.CTkLabel(
            top_row,
            text="System & File Status",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f8fafc"
        )
        status_title.pack(side="left")

        refresh_btn = ctk.CTkButton(
            top_row,
            text="Refresh",
            width=75,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self.refresh_environment_status
        )
        refresh_btn.pack(side="right")

        # Warning / OK banner for MOO2 original game files
        # Notice: File existence check tests for 'orion2.exe' while UI text avoids referencing the raw binary name
        self.moo2_banner = ctk.CTkFrame(self.status_frame, corner_radius=6, fg_color="#451a1a", border_width=1, border_color="#ef4444")
        self.moo2_banner.pack(fill="x", padx=14, pady=2, ipady=4)

        self.moo2_icon_lbl = ctk.CTkLabel(
            self.moo2_banner,
            text="⚠️",
            font=ctk.CTkFont(size=16),
            width=28
        )
        self.moo2_icon_lbl.pack(side="left", padx=(8, 4))

        self.moo2_status_lbl = ctk.CTkLabel(
            self.moo2_banner,
            text="Checking game files...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#fecdd3",
            justify="left",
            wraplength=540
        )
        self.moo2_status_lbl.pack(side="left", padx=4, fill="x", expand=True)

        # Orion2Re executable status row
        self.exe_row = ctk.CTkFrame(self.status_frame, fg_color="transparent")
        self.exe_row.pack(fill="x", padx=14, pady=(4, 0))

        self.exe_icon_lbl = ctk.CTkLabel(self.exe_row, text="🔹", width=20)
        self.exe_icon_lbl.pack(side="left")

        self.exe_status_lbl = ctk.CTkLabel(
            self.exe_row,
            text="Checking Orion2Re engine executable status...",
            font=ctk.CTkFont(size=11),
            text_color="#cbd5e1"
        )
        self.exe_status_lbl.pack(side="left", padx=4)

    def _build_download_card(self, parent):
        """
        Renders the Orion2Re automated build download and update card.
        """
        card = ctk.CTkFrame(parent, corner_radius=8, fg_color="#1e293b", border_width=1, border_color="#334155")
        card.pack(fill="x", pady=(0, 6), ipady=3)

        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(fill="x", padx=14, pady=(0, 2))

        title = ctk.CTkLabel(
            title_row,
            text="Orion2Re Auto-Download & Update",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f8fafc"
        )
        title.pack(side="left")

        about_btn = ctk.CTkButton(
            title_row,
            text="About",
            width=58,
            height=20,
            font=ctk.CTkFont(size=10),
            fg_color="#334155",
            hover_color="#475569",
            command=self.show_build_about
        )
        about_btn.pack(side="right")

        desc = ctk.CTkLabel(
            card,
            text="Fetches the latest Orion2Re build from GitHub Actions.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        desc.pack(anchor="w", padx=14, pady=(0, 3))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 3))

        self.download_btn = ctk.CTkButton(
            btn_row,
            text="Download Orion2Re (Latest Build)",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=30,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self.start_download
        )
        self.download_btn.pack(side="left", fill="x", expand=True)

        # Progress bar configured with corner_radius=0 to avoid false minimum visual fill when value is 0.0
        self.prog_bar = ctk.CTkProgressBar(
            card,
            orientation="horizontal",
            mode="determinate",
            height=5,
            corner_radius=0,
            fg_color="#334155",
            progress_color="#2563eb"
        )
        self.prog_bar.pack(fill="x", padx=14, pady=(3, 2))
        self.prog_bar.set(0.0)

        self.download_status_lbl = ctk.CTkLabel(
            card,
            text="Ready",
            font=ctk.CTkFont(size=10),
            text_color="#94a3b8"
        )
        self.download_status_lbl.pack(anchor="w", padx=14)

    def _build_settings_card(self, parent):
        """
        Renders the configuration editor grid for 'data/orion2re.cfg'.
        """
        card = ctk.CTkFrame(parent, corner_radius=8, fg_color="#1e293b", border_width=1, border_color="#334155")
        card.pack(fill="x", pady=(0, 6), ipady=3)

        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(0, 4))

        title = ctk.CTkLabel(
            top_row,
            text="Engine & Display Settings (data/orion2re.cfg)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f8fafc"
        )
        title.pack(side="left")

        # Two-column grid of engine options
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=1)
        grid.grid_columnconfigure(1, weight=1)

        # Option 1: Active Mod selection & Multi-Mod Manager
        ctk.CTkLabel(grid, text="Active Mods:", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=0, sticky="w", pady=2)
        
        mod_frame = ctk.CTkFrame(grid, fg_color="transparent")
        mod_frame.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=2)
        mod_frame.grid_columnconfigure(0, weight=1)

        self.mod_combo = ctk.CTkComboBox(
            mod_frame,
            values=self._get_mod_combo_values(),
            state="readonly",
            height=24,
            command=self._on_mod_combo_selected
        )
        self.mod_combo.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.mod_order_btn = ctk.CTkButton(
            mod_frame,
            text="⚙ Multi-Mod & Order",
            width=135,
            height=24,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self.open_mod_manager
        )
        self.mod_order_btn.grid(row=0, column=1, sticky="e")

        # Option 2: Display Scaler selection ((Default), hq2x)
        ctk.CTkLabel(grid, text="Display Scaler:", font=ctk.CTkFont(size=11, weight="bold")).grid(row=1, column=0, sticky="w", pady=2)
        self.scaler_combo = ctk.CTkComboBox(grid, values=self.config_mgr.SCALER_OPTIONS, state="readonly", width=360, height=24)
        self.scaler_combo.set(self.config_mgr.scaler)
        self.scaler_combo.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=2)

        # Option 3: Display Mode (Windowed / Fullscreen)
        ctk.CTkLabel(grid, text="Display Mode:", font=ctk.CTkFont(size=11, weight="bold")).grid(row=2, column=0, sticky="w", pady=2)
        self.display_combo = ctk.CTkComboBox(grid, values=self.config_mgr.DISPLAY_MODES, state="readonly", width=360, height=24)
        self.display_combo.set("Fullscreen" if self.config_mgr.fullscreen else "Windowed")
        self.display_combo.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=2)

        # Option 4: Resolution preset selection
        ctk.CTkLabel(grid, text="Resolution:", font=ctk.CTkFont(size=11, weight="bold")).grid(row=3, column=0, sticky="w", pady=2)
        self.res_combo = ctk.CTkComboBox(grid, values=self.config_mgr.RESOLUTION_PRESETS, state="readonly", width=360, height=24)
        self.res_combo.set(self.config_mgr.resolution)
        self.res_combo.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=2)

        # Option 5: Vertical Synchronization switch
        ctk.CTkLabel(grid, text="VSync:", font=ctk.CTkFont(size=11, weight="bold")).grid(row=4, column=0, sticky="w", pady=2)
        self.vsync_switch = ctk.CTkSwitch(grid, text="Enable VSync", onvalue=True, offvalue=False, font=ctk.CTkFont(size=11))
        if self.config_mgr.vsync:
            self.vsync_switch.select()
        else:
            self.vsync_switch.deselect()
        self.vsync_switch.grid(row=4, column=1, sticky="w", padx=(12, 0), pady=2)

        # Option 6: Multiplayer router host address
        ctk.CTkLabel(grid, text="Router Address:", font=ctk.CTkFont(size=11, weight="bold")).grid(row=5, column=0, sticky="w", pady=2)
        self.router_entry = ctk.CTkEntry(grid, width=360, height=24, font=ctk.CTkFont(size=11))
        self.router_entry.insert(0, self.config_mgr.router or self.config_mgr.DEFAULT_ROUTER)
        self.router_entry.grid(row=5, column=1, sticky="ew", padx=(12, 0), pady=2)

        # Action Buttons row: Reset Settings & Save Settings
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=(5, 0))

        self.save_btn = ctk.CTkButton(
            btn_frame,
            text="Save Settings",
            width=105,
            height=24,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=self.save_settings
        )
        self.save_btn.pack(side="right")

        self.reset_btn = ctk.CTkButton(
            btn_frame,
            text="Reset Settings",
            width=105,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#475569",
            hover_color="#64748b",
            command=self.reset_settings
        )
        self.reset_btn.pack(side="right", padx=(0, 8))

    def _build_play_section(self, parent):
        """
        Renders the bottom anchored launch control section with prominent PLAY button
        and version indicator at the bottom-right corner.
        """
        bottom_bar = ctk.CTkFrame(parent, fg_color="#0b1120", corner_radius=0, height=54)
        bottom_bar.pack(side="bottom", fill="x")

        center_frame = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        center_frame.pack(expand=True, pady=6)

        self.play_btn = ctk.CTkButton(
            center_frame,
            text="▶   P L A Y",
            font=ctk.CTkFont(size=17, weight="bold"),
            width=280,
            height=38,
            corner_radius=19,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self.on_play_clicked
        )
        self.play_btn.pack()

        # Version label displayed at the bottom-right corner of the launcher
        ver_lbl = ctk.CTkLabel(
            bottom_bar,
            text="Ver.1.0",
            font=ctk.CTkFont(size=10),
            text_color="#64748b"
        )
        ver_lbl.place(relx=1.0, rely=0.7, anchor="e", x=-14)

    # ---------------- Environment & Status Checks ---------------- #
    def has_original_moo2_files(self):
        """
        Checks for the presence of original Master of Orion II game data files in the root folder.
        Verified by testing for the existence of 'orion2.exe' (case-insensitive).
        
        Notice: In accordance with project requirements, the raw filename 'orion2.exe'
        is NOT displayed in the UI text.
        
        Returns:
            bool: True if original game files are present, False otherwise.
        """
        if not os.path.isdir(self.root_dir):
            return False
        try:
            for fname in os.listdir(self.root_dir):
                if fname.lower() == "orion2.exe":
                    return True
        except Exception as e:
            print(f"[Launcher] Error checking directory files: {e}")
        return False

    def has_orion2re_exe(self):
        """
        Checks if orion2re.exe exists in the application root directory.
        
        Returns:
            bool: True if the engine executable exists, False otherwise.
        """
        return os.path.isfile(self.exe_path)

    def refresh_environment_status(self):
        """
        Refreshes visual status cards, file checks, and the dynamically scanned mod list.
        """
        has_moo2 = self.has_original_moo2_files()
        has_exe = self.has_orion2re_exe()

        # Update original game files status banner (without raw binary name exposure)
        if has_moo2:
            self.moo2_banner.configure(fg_color="#143322", border_color="#10b981")
            self.moo2_icon_lbl.configure(text="✅")
            self.moo2_status_lbl.configure(
                text="Original Master of Orion II game data files verified.",
                text_color="#a7f3d0"
            )
        else:
            self.moo2_banner.configure(fg_color="#451a1a", border_color="#ef4444")
            self.moo2_icon_lbl.configure(text="⚠️")
            self.moo2_status_lbl.configure(
                text="Warning: Original Master of Orion II game files not found in the root directory.\n"
                     "Please manually copy your original game files to this directory.",
                text_color="#fca5a5"
            )

        # Update Orion2Re engine executable status row
        if has_exe:
            size_mb = os.path.getsize(self.exe_path) / (1024 * 1024)
            self.exe_icon_lbl.configure(text="✅")
            self.exe_status_lbl.configure(
                text=f"Orion2Re executable ready (orion2re.exe - {size_mb:.1f} MB)",
                text_color="#93c5fd"
            )
        else:
            self.exe_icon_lbl.configure(text="❌")
            self.exe_status_lbl.configure(
                text="Orion2Re executable not found. Click 'Download Orion2Re' above to install.",
                text_color="#f87171"
            )

        # Dynamically refresh the active mods combobox list
        self._refresh_mod_ui()

    # ---------------- Configuration & Mod Handling ---------------- #
    def open_mod_manager(self):
        """
        Opens the dedicated modal Mod Priority & Multi-Mod Manager dialog.
        """
        ModOrderDialog(self, self.config_mgr, on_apply=self._refresh_mod_ui)

    def _get_mod_combo_values(self):
        """
        Builds the dropdown choices for the quick mod selector.
        If multiple mods are active, includes a summary entry displaying the active load order.
        """
        values = []
        if len(self.config_mgr.mods) > 1:
            summary = ", ".join(self.config_mgr.mods) + f" ({len(self.config_mgr.mods)} mods)"
            values.append(summary)

        values.append("None (Vanilla)")
        for m in self.config_mgr.get_available_mods():
            if m not in values and m != "None (Vanilla)":
                values.append(m)
        return values

    def _on_mod_combo_selected(self, choice):
        """
        Handles selection changes from the quick mod combobox.
        """
        if choice.endswith("mods)"):
            return  # Already multi-mod selection
        elif choice == "None (Vanilla)":
            self.config_mgr.mods = []
        else:
            self.config_mgr.mods = [choice]
        self._refresh_mod_ui()

    def _refresh_mod_ui(self):
        """
        Updates the active mod combobox dropdown values and current selection.
        """
        values = self._get_mod_combo_values()
        self.mod_combo.configure(values=values)
        if len(self.config_mgr.mods) > 1:
            display_text = ", ".join(self.config_mgr.mods) + f" ({len(self.config_mgr.mods)} mods)"
            self.mod_combo.set(display_text)
        elif len(self.config_mgr.mods) == 1:
            self.mod_combo.set(self.config_mgr.mods[0])
        else:
            self.mod_combo.set("None (Vanilla)")

    def reset_settings(self):
        """
        Resets UI controls and data/orion2re.cfg to recommended defaults.
        """
        self.config_mgr.reset_defaults()

        # Refresh UI controls to match default configuration values
        self._refresh_mod_ui()
        self.scaler_combo.set(self.config_mgr.scaler)
        self.display_combo.set("Windowed")
        self.res_combo.set(self.config_mgr.resolution)
        self.vsync_switch.deselect()
        self.router_entry.delete(0, "end")
        self.router_entry.insert(0, self.config_mgr.DEFAULT_ROUTER)

        self.download_status_lbl.configure(text="Settings have been reset to defaults.")

    def save_settings(self):
        """
        Gathers current UI selections and writes them to 'data/orion2re.cfg'.
        
        Returns:
            bool: True on successful write, False otherwise.
        """
        cur_mod = self.mod_combo.get()
        if not cur_mod.endswith("mods)"):
            if cur_mod == "None (Vanilla)":
                self.config_mgr.mods = []
            else:
                self.config_mgr.mods = [cur_mod]

        self.config_mgr.scaler = self.scaler_combo.get()
        self.config_mgr.fullscreen = (self.display_combo.get() == "Fullscreen")
        self.config_mgr.resolution = self.res_combo.get()
        self.config_mgr.vsync = bool(self.vsync_switch.get())
        self.config_mgr.router = self.router_entry.get().strip()

        success = self.config_mgr.save()
        if success:
            self.download_status_lbl.configure(text="Settings saved to data/orion2re.cfg successfully.")
        return success

    # ---------------- Download Handling ---------------- #
    def show_build_about(self):
        """
        Displays acknowledgment and source repository information regarding the automated build action.
        """
        messagebox.showinfo(
            "About Orion2Re Builds",
            "This launcher downloads automated builds from:\n"
            "https://github.com/starwindz/build_orion2re/actions/workflows/build.yml\n\n"
            "Forked from:\n"
            "https://github.com/Ajaja/build_orion2re/actions/workflows/build.yml\n\n"
            "Special thanks to Ajaja for creating and providing the original automated build actions!"
        )

    def start_download(self):
        """
        Initiates asynchronous build download in a background thread to prevent UI freezing.
        """
        if self.is_downloading:
            return

        self.is_downloading = True
        self.download_btn.configure(state="disabled", text="Downloading build...")
        self.prog_bar.set(0.0)
        self.download_status_lbl.configure(text="Searching for latest build on GitHub...")

        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        """
        Worker thread for discovering, streaming, and extracting the latest Orion2Re build.
        """
        try:
            def on_status(msg):
                self.after(0, lambda: self.download_status_lbl.configure(text=msg))

            def on_progress(cur, total):
                if total > 0:
                    pct = cur / total
                    cur_mb = cur / (1024 * 1024)
                    tot_mb = total / (1024 * 1024)
                    text = f"Downloading: {cur_mb:.2f} MB / {tot_mb:.2f} MB ({int(pct * 100)}%)"
                else:
                    text = f"Downloading: {cur / (1024 * 1024):.2f} MB"
                self.after(0, lambda: [
                    self.prog_bar.set(pct if total > 0 else 0.5),
                    self.download_status_lbl.configure(text=text)
                ])

            downloader = Orion2ReDownloader(
                target_dir=self.root_dir,
                status_callback=on_status,
                progress_callback=on_progress
            )
            run_id, artifact = downloader.download_and_extract()

            def on_success():
                self.download_btn.configure(state="normal", text="Download Orion2Re (Latest Build)")
                self.download_status_lbl.configure(text=f"Installed: {artifact}")
                self.prog_bar.set(1.0)
                self.is_downloading = False
                self.config_mgr.load()
                self.refresh_environment_status()
                messagebox.showinfo(
                    "Download Completed",
                    f"Orion2Re latest build has been installed successfully!\n\nArtifact: {artifact}"
                )

            self.after(0, on_success)
        except Exception as e:
            def on_error(err_msg):
                self.download_btn.configure(state="normal", text="Download Orion2Re (Latest Build)")
                self.download_status_lbl.configure(text=f"Error: {err_msg}")
                self.prog_bar.set(0.0)
                self.is_downloading = False
                messagebox.showerror("Download Error", f"Failed to download build:\n\n{err_msg}")

            self.after(0, lambda: on_error(str(e)))

    # ---------------- Play Game Handling ---------------- #
    def on_play_clicked(self):
        """
        Handles user click on the PLAY button.
        Verifies asset readiness, auto-saves settings, minimizes the launcher,
        and launches orion2re.exe synchronously.
        """
        if self.is_game_running:
            return

        # Check original game assets readiness
        if not self.has_original_moo2_files():
            res = messagebox.askyesno(
                "Missing Game Files",
                "Warning: Original Master of Orion II game data files were not detected in the current directory.\n\n"
                "The game engine may fail to run without original assets.\n"
                "Do you still wish to attempt launching?"
            )
            if not res:
                return

        # Check engine binary readiness
        if not self.has_orion2re_exe():
            messagebox.showerror(
                "Executable Missing",
                "orion2re.exe was not found.\n"
                "Please click 'Download Orion2Re' first to install the game engine."
            )
            return

        # Auto-save configuration before executing the game
        self.save_settings()

        self.is_game_running = True
        self.play_btn.configure(state="disabled", text="Running Game...", fg_color="#475569")

        # Minimize launcher window before the game window initializes
        self.iconify()

        # Execute game in a background worker thread to ensure synchronous execution
        # without blocking the OS event pump
        threading.Thread(target=self._launch_game_worker, daemon=True).start()

    def _launch_game_worker(self):
        """
        Synchronously runs 'orion2re.exe' within the application root directory.
        Restores window state when game execution finishes.
        """
        target_exe = os.path.join(self.root_dir, "orion2re.exe")
        try:
            print(f"[Launcher] Running {target_exe} synchronously from {self.root_dir}...")
            # Run synchronously, ensuring working directory is explicitly the application root
            subprocess.run([target_exe], cwd=self.root_dir, check=False)
            print("[Launcher] Game execution finished.")
        except Exception as e:
            print(f"[Launcher] Error executing {target_exe}: {e}")
            self.after(0, lambda: messagebox.showerror("Execution Error", f"An error occurred while launching:\n{e}"))
        finally:
            self.after(0, self._on_game_exited)

    def _on_game_exited(self):
        """
        Restores launcher window to normal presentation and brings it to the foreground.
        """
        self.is_game_running = False
        self.play_btn.configure(state="normal", text="▶   P L A Y", fg_color="#2563eb")
        self.refresh_environment_status()

        # Restore window from minimized state and re-focus
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
        except Exception as e:
            print(f"[Launcher] Failed to restore window: {e}")


def main():
    """
    Application main function.
    """
    app = Orion2Launcher()
    app.mainloop()


if __name__ == "__main__":
    main()
