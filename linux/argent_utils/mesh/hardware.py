"""Machine-strength auto-detection from local hardware specs.

The mesh ranks nodes by *strength* (tier 1 = strongest) so ``weakest-first``
routing keeps a powerful interactive machine free. Rather than make the operator
guess a number, a fresh node measures the box it runs on — RAM, logical CPU
cores, and whether a discrete GPU is present — and maps that to a 1..5 tier once,
on first run (:func:`argent_utils.mesh.identity.load`). A manual edit in the panel
pins the tier and turns auto-detection off (``strengthAuto=False``).

Stdlib-only and best-effort on both platforms the node runs on (Linux + macOS);
every probe degrades to a neutral value on failure, so detection never raises and
an undetectable box lands on the ``tiers.default`` from ``core/mesh.json``.
"""

from __future__ import annotations

import os
import platform as _platform
import subprocess

from . import config


def total_ram_gb() -> float | None:
    """Physical RAM in GiB, or None if it can't be read."""
    system = _platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        kb = float(line.split()[1])  # value is in kB
                        return kb / (1024.0 * 1024.0)
        elif system == "Darwin":
            out = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=2.0,
            )
            if out.returncode == 0 and out.stdout.strip():
                return float(out.stdout.strip()) / (1024.0 ** 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def cpu_cores() -> int | None:
    """Logical CPU count, or None."""
    try:
        return os.cpu_count()
    except Exception:  # noqa: BLE001 — cpu_count is documented not to raise, but be safe
        return None


def has_discrete_gpu() -> bool:
    """Best-effort: does this box have a discrete GPU (a rough proxy for real
    compute headroom)? Never raises; a False negative just nudges the tier one
    step softer, which is harmless."""
    system = _platform.system()
    try:
        if system == "Linux":
            drm = "/sys/class/drm"
            if os.path.isdir(drm):
                for name in os.listdir(drm):
                    # cardN (not cardN-<connector>) with a vendor we recognise as a
                    # dGPU. Integrated Intel graphics also expose a card, so key on
                    # NVIDIA/AMD vendor ids, which are effectively always discrete here.
                    if not (name.startswith("card") and "-" not in name):
                        continue
                    vendor_path = os.path.join(drm, name, "device", "vendor")
                    try:
                        with open(vendor_path, encoding="utf-8") as fh:
                            vendor = fh.read().strip().lower()
                    except OSError:
                        continue
                    if vendor in ("0x10de", "0x1002"):  # NVIDIA, AMD
                        return True
            return os.path.exists("/proc/driver/nvidia/version")
        if system == "Darwin":
            out = subprocess.run(  # noqa: S603,S607
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=6.0,
            )
            if out.returncode == 0:
                text = out.stdout.lower()
                # Apple Silicon integrated GPUs report as "Apple M…"; a discrete
                # card names AMD/NVIDIA/Radeon. Either way, an Apple-Silicon box is
                # strong, so treat an Apple GPU as "capable" too.
                return any(k in text for k in ("radeon", "amd", "nvidia", "apple m"))
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def strength_score(ram_gb: float | None, cores: int | None, dgpu: bool) -> int:
    """Combine specs into a 0..6 strength score (higher = stronger). Pure, so the
    thresholds are unit-testable without touching the machine."""
    score = 0
    # RAM: the dominant signal for how much parallel agent work a box can hold.
    if ram_gb is not None:
        if ram_gb >= 64:
            score += 3
        elif ram_gb >= 32:
            score += 2
        elif ram_gb >= 16:
            score += 1
    # Cores: throughput for concurrent jobs.
    if cores is not None:
        if cores >= 16:
            score += 2
        elif cores >= 8:
            score += 1
    # A discrete / Apple-Silicon GPU is a decent proxy for a workstation-class box.
    if dgpu:
        score += 1
    return score


def _score_to_tier(score: int, lo: int, hi: int) -> int:
    """Map the 0..6 score onto the [lo, hi] tier scale (1 = strongest, so a high
    score yields a low tier number)."""
    # score 6→tier 1 (strongest) … score 0→tier 5 (weakest), clamped to bounds.
    tier = hi - round(score / 6.0 * (hi - lo))
    return max(lo, min(hi, tier))


def detect_tier() -> int:
    """This machine's auto-detected strength tier (``tiers.min``..``tiers.max``,
    1 = strongest). ``ARGENT_MESH_TIER`` forces a value (tests / manual pinning at
    the process level); an undetectable box falls back to ``tiers.default``."""
    lo, hi, default = config.tier_bounds()
    forced = os.environ.get("ARGENT_MESH_TIER")
    if forced is not None:
        try:
            return max(lo, min(hi, int(forced)))
        except ValueError:
            pass
    ram = total_ram_gb()
    cores = cpu_cores()
    if ram is None and cores is None:
        return default  # nothing to go on — neutral
    return _score_to_tier(strength_score(ram, cores, has_discrete_gpu()), lo, hi)
