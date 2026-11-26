"""Manage Jobhunt's optional per-user macOS login service."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from workflow import DATA, ROOT, atomic

LABEL = "local.jobhunt.dashboard"
SERVICE = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


def launchctl(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], check=check, capture_output=True, text=True)


def main():
    if sys.platform != "darwin" or sys.argv[1:] not in (["install"], ["remove"]):
        raise SystemExit("Usage on macOS: .venv/bin/python login_service.py install|remove")
    target = f"{DOMAIN}/{LABEL}"
    active = launchctl("print", target, check=False).returncode == 0
    if active:
        launchctl("bootout", target)
    if sys.argv[1] == "remove":
        SERVICE.unlink(missing_ok=True)
        print("Jobhunt login service removed.")
        return
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(ROOT / "launch.command"), "--background"],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(DATA / "server.log"),
        "StandardErrorPath": str(DATA / "server.log"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "PYTHONPYCACHEPREFIX": str(DATA / "pycache"),
            **{key: os.environ[key] for key in ("JOBHUNT_CODEX", "JOBHUNT_PORT") if key in os.environ},
        },
    }
    atomic(SERVICE, plistlib.dumps(config))
    launchctl("bootstrap", DOMAIN, str(SERVICE))
    print("Jobhunt will run at login. Set the scan time in the dashboard.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.strip() or str(error))
