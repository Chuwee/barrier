#!/usr/bin/env python3
"""Install or remove Edge Lab as a per-user macOS LaunchAgent."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path


LABEL = "com.uc-edge-lab.agent"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "UC Edge Lab"


def plist_value() -> dict:
    script = Path(__file__).with_name("uc_edge_poc.py").resolve()
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(script), "--no-browser"],
        "WorkingDirectory": str(script.parent),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(LOG_DIR / "agent.log"),
        "StandardErrorPath": str(LOG_DIR / "agent-error.log"),
    }


def launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *arguments],
        check=check,
        text=True,
        capture_output=True,
    )


def install() -> None:
    if ".venv" not in Path(sys.executable).parts:
        raise SystemExit("Run with `uv run install_launch_agent.py` so the agent uses its venv.")
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist_value(), handle, sort_keys=False)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", domain, str(PLIST_PATH), check=False)
    result = launchctl("bootstrap", domain, str(PLIST_PATH), check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"LaunchAgent written but could not start: {detail}")
    print(f"Installed and started {LABEL}")
    print("UI: http://127.0.0.1:8765")


def uninstall() -> None:
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", domain, str(PLIST_PATH), check=False)
    PLIST_PATH.unlink(missing_ok=True)
    print(f"Removed {LABEL}")


def status() -> None:
    result = launchctl("print", f"gui/{os.getuid()}/{LABEL}", check=False)
    if result.returncode:
        raise SystemExit(f"{LABEL} is not loaded")
    print(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    args = parser.parse_args()
    {"install": install, "uninstall": uninstall, "status": status}[args.action]()


if __name__ == "__main__":
    main()
