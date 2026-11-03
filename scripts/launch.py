"""Start the pm-jev MCP server inside a private virtual environment.

Claude Code runs this when the plugin loads. On first run (or when requirements.txt
changes) it creates a venv in the plugin's persistent data folder and installs the
dependencies there, so nothing is installed into the user's system Python.

stdout belongs to the MCP protocol, so every message here goes to stderr.

Set PM_JEV_NO_VENV=1 to skip the venv and use the current interpreter instead.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"


def log(msg: str) -> None:
    print(f"pm-jev launcher: {msg}", file=sys.stderr, flush=True)


def data_dir() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(Path.home(), ".cache", "pm-decisions")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def venv_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env() -> Path:
    env_dir = data_dir() / "venv"
    stamp = data_dir() / "requirements.sha256"
    want = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    python = venv_python(env_dir)
    if python.exists() and stamp.exists() and stamp.read_text().strip() == want:
        return python
    log(f"setting up dependencies in {env_dir} (first run only)")
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(env_dir)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)],
        check=True, stdout=sys.stderr, stderr=sys.stderr,
    )
    stamp.write_text(want)
    return python


def main() -> int:
    if sys.version_info < (3, 10):
        log("Python 3.10 or newer is required")
        return 1
    python = Path(sys.executable) if os.environ.get("PM_JEV_NO_VENV") == "1" else ensure_env()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH", "")]))
    # An empty user_config value arrives as an empty string; drop it so the mock backend is chosen cleanly.
    if not (env.get("TYPESAFE_API_KEY") or "").strip():
        env.pop("TYPESAFE_API_KEY", None)
    cmd = [str(python), "-m", "pm_jev.server"]
    if os.name == "nt":
        return subprocess.call(cmd, env=env)
    os.execve(str(python), cmd, env)
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
