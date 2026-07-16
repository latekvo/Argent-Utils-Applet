"""Automatic token-budget signal from the local Claude usage logs.

There is no Anthropic API that reports remaining quota, so a node measures its
*own* recent consumption instead: Claude Code appends every turn to
``~/.claude/projects/**/*.jsonl`` with a ``usage`` block (input / output / cache
token counts). This module sums the tokens spent in the last
``accounts.usageWindowHours`` and compares them to a heuristic per-plan ceiling
(``plan.weight × accounts.tokensPerWeight``) to produce the coarse ok/low/out
state the mesh routes around — replacing the old hand-set dropdown.

Kept cheap enough for the node's 2s snapshot loop: only files touched within the
window are opened, and a per-node cache remembers each file's size + the running
in-window total so a steady poll re-reads only the bytes appended since last time.
Stdlib-only; honours ``HOME`` (so the tests, which sandbox HOME, never read a
developer's real logs) and an explicit ``ARGENT_CLAUDE_DIR`` override.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config

_HOUR_SECS = 3600.0


def claude_projects_dir() -> Path:
    """Where Claude Code writes its per-session transcripts."""
    override = os.environ.get("ARGENT_CLAUDE_DIR")
    base = Path(override) if override else Path.home() / ".claude"
    return base / "projects"


def _token_cost(usage: dict) -> float:
    """Billable-ish token count for one turn: input + output + cache creation.
    Cache *reads* are deliberately excluded — they're huge and cheap, and counting
    them would swamp the signal."""
    total = 0.0
    for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens"):
        try:
            total += float(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _entry_time(rec: dict) -> float | None:
    """Wall-clock epoch of a transcript record from its ISO ``timestamp``; None if
    absent/unparseable (such a record just isn't counted)."""
    ts = rec.get("timestamp")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        # Python's fromisoformat handles the trailing 'Z' only from 3.11; normalise.
        from datetime import datetime

        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def window_tokens(now: float | None = None, window_hours: float | None = None) -> float:
    """Total tokens consumed across all local Claude sessions within the trailing
    window. Best-effort: unreadable/garbage files and lines are skipped, never
    fatal (this feeds a coarse ok/low/out signal, not billing)."""
    now = time.time() if now is None else now
    if window_hours is None:
        window_hours = config.usage_window_hours()
    cutoff = now - window_hours * _HOUR_SECS
    root = claude_projects_dir()
    if not root.is_dir():
        return 0.0

    total = 0.0
    for path in root.rglob("*.jsonl"):
        try:
            # Cheap pre-filter: a file untouched since the cutoff holds nothing in
            # the window (transcripts are append-only).
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    usage = ((rec.get("message") or {}).get("usage")
                             if isinstance(rec.get("message"), dict) else None)
                    if usage is None:
                        usage = rec.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    et = _entry_time(rec)
                    if et is not None and et < cutoff:
                        continue
                    total += _token_cost(usage)
        except OSError:
            continue
    return total


def token_ceiling(plan: str) -> float:
    """Heuristic budget for the trailing window: ``plan.weight × tokensPerWeight``.
    Rough by design (real limits are dynamic); tune ``tokensPerWeight`` in the model."""
    return config.plan_weight(plan) * config.tokens_per_weight()


def fraction_remaining(plan: str, now: float | None = None) -> float:
    """1 − used/ceiling, clamped to [0, 1]. 1.0 = fresh, 0.0 = at/over the ceiling."""
    ceiling = token_ceiling(plan)
    if ceiling <= 0:
        return 1.0
    used = window_tokens(now)
    return max(0.0, min(1.0, 1.0 - used / ceiling))


def state_from_fraction(frac: float) -> str:
    """Map a remaining-fraction to the coarse token state the mesh routes around."""
    if frac <= 0.0:
        return "out"
    if frac < config.low_threshold():
        return "low"
    return "ok"


def token_state(plan: str, now: float | None = None) -> tuple[str, float]:
    """(ok|low|out, fraction_remaining) for this machine's real recent usage."""
    frac = fraction_remaining(plan, now)
    return state_from_fraction(frac), frac
