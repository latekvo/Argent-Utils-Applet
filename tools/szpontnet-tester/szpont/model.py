"""The SzpontNet shared model: protocol constants and the duty/strategy catalog.

Loaded from ``core/mesh.json`` when the tester runs inside the reference
repository, and otherwise falls back to the canonical v1 defaults tabulated in
``docs/szpontnet/appendix-b-constants.md``. Keeping a self-contained fallback
means the tester is a single portable artifact: a second implementation in any
language can copy this directory and run it without the reference repo present.

Everything here is derived from the specification, not from the reference
node's Python source — the tester validates the *wire*, not an implementation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROTOCOL_VERSION = 1

# Canonical v1 constants (appendix B). Used verbatim unless core/mesh.json is
# found, in which case its "protocol" block overrides the timing/discovery keys.
DEFAULT_PROTOCOL = {
    "version": 1,
    "multicastGroup": "239.83.77.7",
    "multicastPort": 40877,
    "tcpPortBase": 40878,
    "tcpPortSpan": 10,
    "beaconIntervalSecs": 2.0,
    "heartbeatIntervalSecs": 2.0,
    "peerStaleSecs": 5.0,
    "peerTimeoutSecs": 10.0,
    "dispatchAckTimeoutSecs": 8.0,
    "stateWriteIntervalSecs": 2.0,
}

MAX_LINE_BYTES = 512 * 1024  # 03-transport framing cap

TIER_MIN, TIER_MAX, TIER_DEFAULT = 1, 5, 3
TOKEN_RANK = {"ok": 0, "low": 1, "out": 2}
TOKEN_STATES = ("ok", "low", "out")
PLATFORMS = ("linux", "macos")
STRATEGIES = ("weakest-first", "strongest-first", "local-first")
DEFAULT_STRATEGY = "weakest-first"

# The v1 duty catalog with default placement policies (appendix B / 05-resources).
DEFAULT_DUTIES = {
    "review": {"strategy": "weakest-first", "tokenAware": True, "spread": []},
    "conflicts": {"strategy": "weakest-first", "tokenAware": True, "spread": []},
    "audit": {
        "strategy": "weakest-first",
        "tokenAware": True,
        "spread": [{"platform": "linux", "count": 1}, {"platform": "macos", "count": 1}],
    },
}


def _find_mesh_json() -> Path | None:
    env = os.environ.get("SZPONTNET_MESH_JSON") or os.environ.get("ARGENT_MESH_JSON")
    if env and Path(env).is_file():
        return Path(env)
    here = Path(__file__).resolve()
    for base in [here] + list(here.parents):
        candidate = base / "core" / "mesh.json"
        if candidate.is_file():
            return candidate
    return None


class Model:
    """Resolved shared model: constants + duty catalog, from mesh.json or defaults."""

    def __init__(self, protocol: dict, duties: dict, source: str) -> None:
        self.protocol = protocol
        self.duties = duties  # duty id -> placement dict
        self.source = source

    @property
    def duty_ids(self) -> list[str]:
        return list(self.duties.keys())

    def placement_for(self, duty_id: str) -> dict:
        return self.duties.get(duty_id, {})


def load_model() -> Model:
    path = _find_mesh_json()
    if path is None:
        return Model(dict(DEFAULT_PROTOCOL), dict(DEFAULT_DUTIES), source="built-in v1 defaults")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        protocol = dict(DEFAULT_PROTOCOL)
        protocol.update(raw.get("protocol", {}))
        duties = {d["id"]: d.get("placement", {}) for d in raw.get("duties", [])}
        if not duties:
            duties = dict(DEFAULT_DUTIES)
        return Model(protocol, duties, source=str(path))
    except (OSError, ValueError, KeyError):
        return Model(dict(DEFAULT_PROTOCOL), dict(DEFAULT_DUTIES), source="built-in v1 defaults")
