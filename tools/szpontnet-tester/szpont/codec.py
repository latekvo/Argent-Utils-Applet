"""The SzpontNet wire codec — a clean-room, spec-faithful NDJSON encoder/decoder.

This is the tester's own reference implementation of chapter 04's message
catalog and chapter 03's framing. It is written from the specification so the
tester can (a) *speak* the protocol correctly as a peer / control client /
adversary and (b) *validate* every message a candidate emits against a strict
schema. Where this codec and a candidate disagree, one of them violates the
spec — which is exactly what the tester is for.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace

from .model import MAX_LINE_BYTES, PROTOCOL_VERSION


# MARK: - NodeInfo (04-messages.md#nodeinfo)


@dataclass(frozen=True)
class NodeInfo:
    id: str
    name: str = "?"
    platform: str = "unknown"
    tier: int = 3
    tokens: str = "ok"
    tcp_port: int = 0
    epoch: float = 0.0
    seq: int = 0
    sees: tuple[str, ...] = ()
    duties_enabled: dict = field(default_factory=dict)
    version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "tier": self.tier,
            "tokens": self.tokens,
            "tcpPort": self.tcp_port,
            "epoch": self.epoch,
            "seq": self.seq,
            "sees": list(self.sees),
            "dutiesEnabled": self.duties_enabled,
            "v": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NodeInfo | None":
        """Parse a NodeInfo the way a conformant receiver must: a missing ``id``
        or an unparseable numeric field invalidates the whole object (04)."""
        if not isinstance(d, dict) or "id" not in d:
            return None
        try:
            return cls(
                id=str(d["id"]),
                name=str(d.get("name", "?")),
                platform=str(d.get("platform", "unknown")),
                tier=int(d.get("tier", 3)),
                tokens=str(d.get("tokens", "ok")),
                tcp_port=int(d.get("tcpPort", 0)),
                epoch=float(d.get("epoch", 0.0)),
                seq=int(d.get("seq", 0)),
                sees=tuple(str(s) for s in d.get("sees", [])),
                duties_enabled=dict(d.get("dutiesEnabled", {})),
                version=int(d.get("v", PROTOCOL_VERSION)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def newer_than(self, other: "NodeInfo") -> bool:
        return (self.epoch, self.seq) > (other.epoch, other.seq)

    def duty_enabled(self, duty_id: str) -> bool:
        return bool(self.duties_enabled.get(duty_id, True))

    def bumped(self, **changes) -> "NodeInfo":
        return replace(self, seq=self.seq + 1, **changes)


# MARK: - Job (04-messages.md#job)


@dataclass(frozen=True)
class Job:
    id: str
    duty: str
    prompt: str = ""
    requested_by: str = "?"
    requested_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "duty": self.duty,
            "prompt": self.prompt,
            "requestedBy": self.requested_by,
            "requestedAt": self.requested_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Job | None":
        if not isinstance(d, dict) or "id" not in d or "duty" not in d:
            return None
        try:
            return cls(
                id=str(d["id"]),
                duty=str(d["duty"]),
                prompt=str(d.get("prompt", "")),
                requested_by=str(d.get("requestedBy", "?")),
                requested_at=float(d.get("requestedAt", time.time())),
            )
        except (KeyError, TypeError, ValueError):
            return None


# MARK: - Envelope encode / decode (03-transport.md#framing)


def encode(msg: dict) -> bytes:
    """One compact NDJSON line, ``\\n``-terminated, with ``v`` defaulted."""
    out = dict(msg)
    out.setdefault("v", PROTOCOL_VERSION)
    return (json.dumps(out, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict | None:
    """Parse one line; ``None`` (drop) for: empty, over-length, invalid UTF-8,
    non-JSON, a non-object, or an object without a string ``t``. This is the
    exact drop-set the conformance vector V2 enumerates."""
    if not line or len(line) > MAX_LINE_BYTES:
        return None
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        return None
    return msg


# MARK: - Message builders


def beacon(info: NodeInfo) -> dict:
    return {
        "t": "beacon",
        "id": info.id,
        "name": info.name,
        "platform": info.platform,
        "tcpPort": info.tcp_port,
        "epoch": info.epoch,
    }


def hello(info: NodeInfo, overrides: dict, secret: str = "") -> dict:
    msg = {"t": "hello", "node": info.to_dict(), "overrides": overrides}
    if secret:
        msg["secret"] = secret
    return msg


def ctl_hello(secret: str = "") -> dict:
    msg: dict = {"t": "ctl"}
    if secret:
        msg["secret"] = secret
    return msg


def heartbeat() -> dict:
    return {"t": "heartbeat", "ts": time.time()}


def node_update(info: NodeInfo) -> dict:
    return {"t": "node", "node": info.to_dict()}


def overrides_update(overrides: dict) -> dict:
    return {"t": "overrides", "overrides": overrides}


def set_attr(target_id: str, attrs: dict) -> dict:
    return {"t": "set-attr", "target": target_id, "attrs": attrs}


def dispatch_job(job: Job) -> dict:
    return {"t": "dispatch", "job": job.to_dict()}


def dispatch_route(duty: str, prompt: str) -> dict:
    return {"t": "dispatch", "duty": duty, "prompt": prompt}


def job_status(job_id: str, status: str, reason: str = "", node_id: str = "") -> dict:
    return {"t": "job-status", "id": job_id, "status": status, "reason": reason, "node": node_id}


def status_request() -> dict:
    return {"t": "status"}


def set_overrides(duty: str, placement: dict) -> dict:
    return {"t": "set-overrides", "duty": duty, "placement": placement}


def stop() -> dict:
    return {"t": "stop"}


# MARK: - Strict validators (checking messages a candidate EMITS)
#
# These enforce the field/type contract from chapter 04. Each returns a list of
# human-readable problems ([] == valid). Used by the codec-conformance suite to
# prove a candidate's emitted beacon / hello / node / heartbeat are on-spec.


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x) -> bool:
    return _is_int(x) or isinstance(x, float)


def validate_envelope(msg: dict) -> list[str]:
    problems = []
    if not isinstance(msg.get("t"), str):
        problems.append("missing/non-string 't'")
    if "v" in msg and not _is_int(msg["v"]):
        problems.append("'v' present but not an int")
    return problems


def validate_nodeinfo(d: dict) -> list[str]:
    problems = []
    if not isinstance(d, dict):
        return ["NodeInfo is not an object"]
    if not isinstance(d.get("id"), str) or not d.get("id"):
        problems.append("NodeInfo.id missing or not a non-empty string")
    if "platform" in d and not isinstance(d["platform"], str):
        problems.append("NodeInfo.platform not a string")
    if "tier" in d and not _is_int(d["tier"]):
        problems.append("NodeInfo.tier not an int")
    if "tokens" in d and not isinstance(d["tokens"], str):
        problems.append("NodeInfo.tokens not a string")
    if "tcpPort" in d and not _is_int(d["tcpPort"]):
        problems.append("NodeInfo.tcpPort not an int")
    if "epoch" in d and not _is_num(d["epoch"]):
        problems.append("NodeInfo.epoch not a number")
    if "seq" in d and not _is_int(d["seq"]):
        problems.append("NodeInfo.seq not an int")
    if "sees" in d and not isinstance(d["sees"], list):
        problems.append("NodeInfo.sees not an array")
    if "dutiesEnabled" in d and not isinstance(d["dutiesEnabled"], dict):
        problems.append("NodeInfo.dutiesEnabled not an object")
    return problems


def validate_beacon(msg: dict) -> list[str]:
    problems = validate_envelope(msg)
    if msg.get("t") != "beacon":
        problems.append(f"expected t=beacon, got {msg.get('t')!r}")
    if not isinstance(msg.get("id"), str) or not msg.get("id"):
        problems.append("beacon.id missing or not a non-empty string")
    port = msg.get("tcpPort")
    if not _is_int(port) or port <= 0:
        problems.append("beacon.tcpPort missing or not a positive int (02-discovery MUST)")
    if "epoch" in msg and not _is_num(msg["epoch"]):
        problems.append("beacon.epoch not a number")
    if "platform" in msg and not isinstance(msg["platform"], str):
        problems.append("beacon.platform not a string")
    return problems


def validate_hello(msg: dict) -> list[str]:
    problems = validate_envelope(msg)
    if msg.get("t") != "hello":
        problems.append(f"expected t=hello, got {msg.get('t')!r}")
    if "node" not in msg:
        problems.append("hello.node (NodeInfo) is required")
    else:
        problems += [f"hello.{p}" for p in validate_nodeinfo(msg["node"])]
    if "overrides" in msg and not isinstance(msg["overrides"], dict):
        problems.append("hello.overrides not an object")
    return problems


def validate_heartbeat(msg: dict) -> list[str]:
    problems = validate_envelope(msg)
    if msg.get("t") != "heartbeat":
        problems.append(f"expected t=heartbeat, got {msg.get('t')!r}")
    return problems


def is_single_line_json(raw: bytes) -> list[str]:
    """A wire frame MUST be one compact UTF-8 JSON line: no interior newline,
    exactly one trailing ``\\n``, decodable as UTF-8 (03-transport)."""
    problems = []
    if not raw.endswith(b"\n"):
        problems.append("frame not newline-terminated")
    body = raw[:-1] if raw.endswith(b"\n") else raw
    if b"\n" in body:
        problems.append("frame has an interior newline")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        problems.append("frame is not valid UTF-8")
    return problems
