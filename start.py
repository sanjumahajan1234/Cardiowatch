#!/usr/bin/env python
"""
CardioWatch All-in-One Launcher (cross-platform)
Starts Flask backend and opens frontend in browser.
"""

import os
import sys
import subprocess
import webbrowser
import time
from pathlib import Path

def check_and_install_deps():
    """Install required Python packages if missing."""
    required = ["flask", "requests", "fhir.resources"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg.replace(".", "_").replace("-", "_"))
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Installing missing Python dependencies:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)

def start_backend():
    """Start Flask app.py in background."""
    env = os.environ.copy()
    env["FLASK_ENV"] = "development"
    env["FLASK_DEBUG"] = "1"
    return subprocess.Popen([sys.executable, "app.py"], env=env)

def open_browser():
    """Open the frontend URL in the default browser."""
    time.sleep(3)  # give Flask a moment to start
    webbrowser.open("http://127.0.0.1:5000")

def main():
    print("=== CardioWatch Launcher ===")
    print("Starting Flask backend...")
    check_and_install_deps()
    proc = start_backend()
    open_browser()
    print(f"Backend PID: {proc.pid}")
    print("Frontend URL: http://127.0.0.1:5000")
    print("Press Ctrl+C to stop the backend")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping backend...")
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
