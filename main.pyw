"""
Master of Orion II - Orion2Re Launcher
Application Entry Point (Window-only GUI execution).

When launched directly via Windows Explorer, the .pyw extension directs Windows
to use pythonw.exe, preventing the creation of an unnecessary command-prompt window.
"""
from launcher import main

if __name__ == "__main__":
    main()
