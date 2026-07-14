"""Bridge to the ``argent-core`` Swift CLI — the single source of truth for prompt
assembly.

The Review/Conflicts/Audit prompts are built by ``ArgentUtilsCore`` (the same code
the macOS app uses). The Linux applet shells out to the compiled ``argent-core``
binary instead of re-implementing that logic in Python, so the two front-ends can
never drift. Build the binary with ``linux/scripts/build-core.sh``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import core


class CoreBinaryMissing(RuntimeError):
    """Raised when the argent-core binary can't be located."""


def core_bin() -> str:
    """Locate the argent-core binary: ``$ARGENT_CORE_BIN``, then ``PATH``, then the
    XDG install location (``~/.local/share/argent-utils/argent-core``)."""
    override = os.environ.get("ARGENT_CORE_BIN")
    if override and os.path.exists(override):
        return override
    found = shutil.which("argent-core")
    if found:
        return found
    data = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    candidate = data / "argent-utils" / "argent-core"
    if candidate.exists():
        return str(candidate)
    raise CoreBinaryMissing(
        "argent-core not found — run linux/scripts/build-core.sh "
        "(or set ARGENT_CORE_BIN)."
    )


def build_prompt(config: dict) -> str:
    """Assemble a prompt by shelling out to argent-core. ``config`` is the JSON
    payload whose ``kind`` is ``review`` | ``conflicts`` | ``audit``."""
    binary = core_bin()
    env = dict(os.environ)
    env.setdefault("ARGENT_UTILS_CORE", str(core.core_dir()))
    proc = subprocess.run(  # noqa: S603 — argv is a literal list, not a shell string
        [binary, "build-prompt"],
        input=json.dumps(config),
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"argent-core failed: {proc.stderr.strip()}")
    return proc.stdout
