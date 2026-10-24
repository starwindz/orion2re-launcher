import os
import sys
import io
import re
import json
import zipfile
import urllib.request
import urllib.error

# Target GitHub repository, release, and workflow constants
GITHUB_REPO = "orion2re/orion2re"
WORKFLOW_FILE = "ci.yml"
GITHUB_WORKFLOW_URL = f"https://github.com/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}"
API_WORKFLOW_RUNS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/runs?status=completed&conclusion=success&per_page=10"
TARGET_ARTIFACT_PATTERNS = [
    "orion2re-win-win32-main.zip",
    "orion2re-win-win32.zip",
    "orion2re-win32.zip",
    "orion2re-win32"
]
TARGET_ARTIFACT_NAME = "orion2re-win-win32-main.zip"

OFFICIAL_RELEASE_DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/orion2re-win-win32.zip"
API_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def get_app_root():
    """
    Returns the absolute path to the root directory where the application is installed.
    Handles both source-based execution and PyInstaller bundled executables.
    
    Returns:
        str: Absolute filesystem path.
    """
    if getattr(sys, "frozen", False):
        return os.path.abspath(os.path.dirname(sys.executable))
    return os.path.abspath(os.path.dirname(__file__))


class Orion2ReDownloader:
    """
    Handles discovering, downloading, and unpacking both Official and Nightly
    builds of the Orion2Re engine:
      1. Official release (orion2re-win-win32.zip) extracted to 'orion2re/official'.
      2. Nightly CI build (orion2re-win-win32-main.zip) extracted to 'orion2re/nightly'.
    """

    def __init__(self, target_dir=None, status_callback=None, progress_callback=None):
        """
        Initializes the downloader instance.

        Args:
            target_dir (str, optional): Target directory to extract files into. Defaults to app root.
            status_callback (callable, optional): Callback accepting a status string.
            progress_callback (callable, optional): Callback accepting (downloaded_bytes, total_bytes).
        """
        self.target_dir = os.path.abspath(target_dir) if target_dir else get_app_root()
        self.status_callback = status_callback
        self.progress_callback = progress_callback
        self._is_cancelled = False

    def cancel(self):
        """
        Signals cancellation of an ongoing download.
        """
        self._is_cancelled = True

    def _notify_status(self, msg):
        """
        Sends status message to the UI callback if registered.
        """
        if self.status_callback:
            self.status_callback(msg)

    def _notify_progress(self, current, total):
        """
        Sends current download byte counts to the progress callback if registered.
        """
        if self.progress_callback:
            self.progress_callback(current, total)

    def _download_stream(self, url, description, start_pct=0.0, end_pct=1.0):
        """
        Streams a remote URL into an in-memory BytesIO buffer with scaled progress reporting.
        
        Args:
            url (str): Remote URL to fetch.
            description (str): Human-readable download description for status logging.
            start_pct (float): Starting fraction of total progress (0.0 to 1.0).
            end_pct (float): Ending fraction of total progress (0.0 to 1.0).
            
        Returns:
            io.BytesIO: Buffer containing downloaded file data.
        """
        self._notify_status(description)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Orion2Re-Launcher/1.0"
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024
            buffer = io.BytesIO()

            while True:
                if self._is_cancelled:
                    raise RuntimeError("Download cancelled by user.")
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                buffer.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = start_pct + (downloaded / total_size) * (end_pct - start_pct)
                    self._notify_progress(int(pct * 1000), 1000)
                else:
                    self._notify_progress(downloaded, 0)

        if self._is_cancelled:
            raise RuntimeError("Download cancelled.")
        buffer.seek(0)
        return buffer

    def _extract_to(self, buffer, dest_dir):
        """
        Extracts a zip archive buffer into dest_dir. If the archive contains a nested
        distribution zip (e.g., orion2re-win-win32-main.zip), unpacks that nested archive instead.
        Mod archives under data/mods/ or data/base.zip remain untouched as zip files.
        """
        os.makedirs(dest_dir, exist_ok=True)
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as outer_z:
            nested_zips = [
                m for m in outer_z.infolist()
                if m.filename.lower().endswith(".zip") and not m.is_dir()
                and not any(m.filename.lower().replace("\\", "/").startswith(p) for p in ("data/", "data\\"))
            ]

            if nested_zips:
                for nz in nested_zips:
                    with zipfile.ZipFile(io.BytesIO(outer_z.read(nz))) as inner_z:
                        inner_z.extractall(dest_dir)
            else:
                outer_z.extractall(dest_dir)

    def get_latest_release_url(self):
        """
        Discovers the direct download URL for the latest official release (orion2re-win-win32.zip).
        
        Returns:
            str: Direct asset download URL.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Orion2Re-Launcher/1.0"
        }
        try:
            req = urllib.request.Request(API_LATEST_RELEASE_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if name == "orion2re-win-win32.zip" or "win32" in name.lower():
                        return asset.get("browser_download_url")
        except Exception as e:
            print(f"[Downloader] Releases API lookup failed: {e}, using direct redirect URL...")

        return OFFICIAL_RELEASE_DOWNLOAD_URL

    def get_latest_build_info(self):
        """
        Discovers the latest successful nightly CI workflow run ID and artifact name.
        Uses GitHub REST API with web scraping fallback.
        
        Returns:
            tuple: (run_id: int, artifact_name: str, download_url: str)
            
        Raises:
            RuntimeError: If no valid build run or artifact can be discovered.
        """
        self._notify_status("Searching for latest nightly build on GitHub...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Orion2Re-Launcher/1.0"
        }

        run_id = None

        # Step 1: Query GitHub REST API for completed workflow runs
        try:
            req = urllib.request.Request(API_WORKFLOW_RUNS_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for run in data.get("workflow_runs", []):
                    if run.get("status") == "completed" and run.get("conclusion") == "success":
                        run_id = run.get("id")
                        break
        except Exception as e:
            print(f"[Downloader] API runs lookup failed: {e}, attempting scraping fallback...")

        # Step 2: Fallback to parsing the GitHub HTML page if the API fails or is rate-limited
        if not run_id:
            try:
                req = urllib.request.Request(GITHUB_WORKFLOW_URL, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    
                    # Search inside React embedded data payload
                    m = re.search(r'<script type="application/json" data-target="react-app\.embeddedData">(.*?)</script>', html)
                    if m:
                        page_data = json.loads(m.group(1))
                        runs = page_data.get("payload", {}).get("workflowRunsRoute", {}).get("workflowRuns", [])
                        if runs:
                            run_id = runs[0].get("id")
                            
                    # Regex fallback for action run URLs
                    if not run_id:
                        found_ids = re.findall(rf'/{GITHUB_REPO}/actions/runs/(\d+)', html)
                        if found_ids:
                            run_id = int(found_ids[0])
            except Exception as e:
                print(f"[Downloader] Web page scraping failed: {e}")

        if not run_id:
            raise RuntimeError(f"Could not find any successful build runs on {GITHUB_REPO}.")

        self._notify_status(f"Found latest nightly build (Run #{run_id}). Locating artifact...")

        # Step 3: Query run artifacts via REST API
        artifact_name = None
        artifacts_api = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/artifacts"
        try:
            req = urllib.request.Request(artifacts_api, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                artifacts = data.get("artifacts", [])
                # Priority 1: Match known artifact naming patterns
                for pattern in TARGET_ARTIFACT_PATTERNS:
                    for a in artifacts:
                        if a.get("name", "").lower() == pattern.lower():
                            artifact_name = a.get("name")
                            break
                    if artifact_name:
                        break
                # Priority 2: Match any artifact containing win32 (and not win64)
                if not artifact_name:
                    for a in artifacts:
                        name = a.get("name", "")
                        if "win32" in name.lower() and "win64" not in name.lower():
                            artifact_name = name
                            break
        except Exception as e:
            print(f"[Downloader] Artifacts API failed: {e}")

        # Web scraping fallback for artifact name from run summary page
        if not artifact_name:
            try:
                run_page_url = f"https://github.com/{GITHUB_REPO}/actions/runs/{run_id}"
                req = urllib.request.Request(run_page_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    m = re.search(r'(orion2re-[a-zA-Z0-9_\-\.]*win32[a-zA-Z0-9_\-\.]*)', html)
                    if m:
                        artifact_name = m.group(1).rstrip()
            except Exception as e:
                print(f"[Downloader] Run page scraping failed: {e}")

        if not artifact_name:
            artifact_name = TARGET_ARTIFACT_NAME

        # Step 4: Construct nightly.link download proxy URL
        clean_name = artifact_name.rstrip()
        if clean_name.lower().endswith(".zip"):
            download_url = f"https://nightly.link/{GITHUB_REPO}/actions/runs/{run_id}/{clean_name}"
        else:
            download_url = f"https://nightly.link/{GITHUB_REPO}/actions/runs/{run_id}/{clean_name}.zip"
        return run_id, artifact_name, download_url

    def download_official(self, start_pct=0.0, end_pct=0.5):
        """
        Downloads and unpacks the official release into 'orion2re/official'.
        """
        official_dest = os.path.join(self.target_dir, "orion2re", "official")
        release_url = self.get_latest_release_url()
        buffer = self._download_stream(
            release_url,
            "Downloading Official Release (orion2re-win-win32.zip)...",
            start_pct=start_pct,
            end_pct=end_pct
        )
        self._notify_status("Extracting Official Release into orion2re/official...")
        self._extract_to(buffer, official_dest)

    def download_nightly(self, start_pct=0.5, end_pct=1.0):
        """
        Downloads and unpacks the latest nightly CI build into 'orion2re/nightly'.
        """
        nightly_dest = os.path.join(self.target_dir, "orion2re", "nightly")
        run_id, artifact_name, download_url = self.get_latest_build_info()
        disp_name = artifact_name if artifact_name.lower().endswith(".zip") else f"{artifact_name}.zip"
        buffer = self._download_stream(
            download_url,
            f"Downloading Nightly Build ({disp_name})...",
            start_pct=start_pct,
            end_pct=end_pct
        )
        self._notify_status("Extracting Nightly Build into orion2re/nightly...")
        self._extract_to(buffer, nightly_dest)
        return run_id, artifact_name

    def download_and_extract(self):
        """
        Performs both download tasks:
          1. Downloads and extracts the official release into 'orion2re/official'.
          2. Downloads and extracts the latest nightly CI build into 'orion2re/nightly'.
          
        Returns:
            tuple: (run_id: int, status_summary: str)
        """
        self._is_cancelled = False

        # 1. Official release download (0% to 50%)
        self.download_official(start_pct=0.0, end_pct=0.5)

        # 2. Nightly CI build download (50% to 100%)
        run_id, artifact_name = self.download_nightly(start_pct=0.5, end_pct=1.0)

        self._notify_status("Official and Nightly builds installed successfully!")
        self._notify_progress(1000, 1000)
        return run_id, "Official & Nightly"
