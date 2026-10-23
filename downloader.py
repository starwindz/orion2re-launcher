import os
import sys
import io
import re
import json
import zipfile
import urllib.request
import urllib.error

# Target GitHub repository and workflow for automated build discovery
GITHUB_WORKFLOW_URL = "https://github.com/starwindz/build_orion2re/actions/workflows/build.yml"
API_WORKFLOW_RUNS_URL = "https://api.github.com/repos/starwindz/build_orion2re/actions/workflows/build.yml/runs?per_page=10"

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
    Handles discovering, downloading, and unpacking the latest Orion2Re engine build.
    
    Workflow:
      1. Inspects GitHub Actions workflow runs via the GitHub REST API (or web scraping fallback).
      2. Identifies the latest successful run and its corresponding Windows artifact.
      3. Downloads the artifact zip using nightly.link proxy (enabling unauthenticated download).
      4. Extracts all files directly into the target project directory.
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

    def get_latest_build_info(self):
        """
        Discovers the latest successful workflow run ID and artifact name.
        Uses GitHub REST API with web scraping fallback.
        
        Returns:
            tuple: (run_id: int, artifact_name: str, download_url: str)
            
        Raises:
            RuntimeError: If no valid build run or artifact can be discovered.
        """
        self._notify_status("Searching for latest build on GitHub...")
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
                        found_ids = re.findall(r'/starwindz/build_orion2re/actions/runs/(\d+)', html)
                        if found_ids:
                            run_id = int(found_ids[0])
            except Exception as e:
                print(f"[Downloader] Web page scraping failed: {e}")

        if not run_id:
            raise RuntimeError("Could not find any successful build runs on starwindz/build_orion2re.")

        self._notify_status(f"Found latest build (Run #{run_id}). Locating artifact...")

        # Step 3: Query run artifacts via REST API
        artifact_name = None
        artifacts_api = f"https://api.github.com/repos/starwindz/build_orion2re/actions/runs/{run_id}/artifacts"
        try:
            req = urllib.request.Request(artifacts_api, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                artifacts = data.get("artifacts", [])
                if artifacts:
                    artifact_name = artifacts[0].get("name")
        except Exception as e:
            print(f"[Downloader] Artifacts API failed: {e}")

        # Web scraping fallback for artifact name from run summary page
        if not artifact_name:
            try:
                run_page_url = f"https://github.com/starwindz/build_orion2re/actions/runs/{run_id}"
                req = urllib.request.Request(run_page_url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    m = re.search(r'(orion2re-[a-zA-Z0-9_\-\.]+)', html)
                    if m:
                        artifact_name = m.group(1).rstrip()
            except Exception as e:
                print(f"[Downloader] Run page scraping failed: {e}")

        if not artifact_name:
            raise RuntimeError(f"Could not locate download artifact for Run #{run_id}.")

        # Step 4: Construct nightly.link download proxy URL
        download_url = f"https://nightly.link/starwindz/build_orion2re/actions/runs/{run_id}/{artifact_name}.zip"
        return run_id, artifact_name, download_url

    def download_and_extract(self):
        """
        Streams the artifact zip from nightly.link with chunked progress reporting
        and unpacks all contents directly into target_dir.
        
        Returns:
            tuple: (run_id: int, artifact_name: str)
            
        Raises:
            RuntimeError: On network error, cancellation, or corrupt zip archive.
        """
        self._is_cancelled = False
        run_id, artifact_name, download_url = self.get_latest_build_info()

        self._notify_status(f"Downloading {artifact_name}.zip...")
        self._notify_progress(0, 100)

        req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024
            buffer = io.BytesIO()

            # Stream chunks to buffer while computing download progress
            while True:
                if self._is_cancelled:
                    raise RuntimeError("Download cancelled by user.")
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                buffer.write(chunk)
                downloaded += len(chunk)
                self._notify_progress(downloaded, total_size)

        if self._is_cancelled:
            raise RuntimeError("Download cancelled.")

        self._notify_status("Extracting archive into project directory...")
        buffer.seek(0)
        os.makedirs(self.target_dir, exist_ok=True)

        # Unpack files into the project directory, preserving existing data/orion2re.cfg if already present
        cfg_rel_path = os.path.normpath("data/orion2re.cfg")
        existing_cfg_content = None
        target_cfg_path = os.path.join(self.target_dir, "data", "orion2re.cfg")
        if os.path.isfile(target_cfg_path):
            try:
                with open(target_cfg_path, "rb") as f:
                    existing_cfg_content = f.read()
            except Exception as e:
                print(f"[Downloader] Warning: could not backup existing config: {e}")

        with zipfile.ZipFile(buffer) as z:
            for member in z.infolist():
                norm_member = os.path.normpath(member.filename)
                # If target cfg already exists, do not overwrite it with the default template from zip
                if existing_cfg_content and norm_member.lower() == cfg_rel_path.lower():
                    continue
                z.extract(member, self.target_dir)

        # Restore existing configuration file to guarantee user settings are preserved
        if existing_cfg_content and os.path.isfile(target_cfg_path):
            try:
                with open(target_cfg_path, "wb") as f:
                    f.write(existing_cfg_content)
            except Exception as e:
                print(f"[Downloader] Error restoring user config: {e}")

        self._notify_status(f"Installed successfully: {artifact_name}")
        self._notify_progress(100, 100)
        return run_id, artifact_name
