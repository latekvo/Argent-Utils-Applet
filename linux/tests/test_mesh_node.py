"""Mesh integration tests: real nodes, real sockets, one machine.

Spins actual ``python -m argent_utils.mesh`` node processes on loopback
(ARGENT_MESH_LOOPBACK=1 keeps every socket on 127.0.0.1; multicast loops back
locally) with fast protocol timings, then asserts the behaviours the design
promises: discovery convergence, deterministic cross-node assignment
agreement, duty takeover when a node dies, remote attribute edits, LWW
placement-override gossip, and per-slot dispatch with token failover.

Each fake node gets its own ARGENT_MESH_DIR (identity + state.json) and a
platform override, so a single Linux CI runner hosts a mixed linux/macos
fleet. Dispatch lands via ARGENT_MESH_SPAWN (a `cp` template) instead of a
real terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LINUX_DIR = Path(__file__).resolve().parents[1]

# Unique-ish ports per run so parallel/leftover runs can't collide.
_PORT_BASE = 42000 + (os.getpid() % 400) * 20


def _proto_env() -> dict:
    return {
        "ARGENT_MESH_LOOPBACK": "1",
        "ARGENT_MESH_MCAST_PORT": str(_PORT_BASE),
        "ARGENT_MESH_TCP_BASE": str(_PORT_BASE + 1),
        "ARGENT_MESH_TCP_SPAN": "12",
        "ARGENT_MESH_BEACON_SECS": "0.25",
        "ARGENT_MESH_HEARTBEAT_SECS": "0.25",
        "ARGENT_MESH_STALE_SECS": "1.0",
        "ARGENT_MESH_TIMEOUT_SECS": "2.0",
        "ARGENT_MESH_ACK_SECS": "4.0",
        "ARGENT_MESH_STATE_SECS": "0.25",
    }


class Fleet:
    """A handful of real mesh-node subprocesses sharing one loopback mesh."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.procs: dict[str, subprocess.Popen] = {}
        self.dirs: dict[str, Path] = {}

    def start(self, node_id: str, name: str, platform: str, tier: int,
              tokens: str = "ok", secret: str = "") -> None:
        d = self.root / node_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "node.json").write_text(json.dumps({
            "id": node_id, "name": name, "tier": tier,
            "tokens": tokens, "dutiesEnabled": {},
        }))
        (self.root / "spawned").mkdir(exist_ok=True)
        env = dict(os.environ)
        env.update(_proto_env())
        env["ARGENT_MESH_DIR"] = str(d)
        env["ARGENT_MESH_PLATFORM"] = platform
        env["ARGENT_MESH_SPAWN"] = f"cp {{prompt_file}} {self.root}/spawned/{name}.txt"
        env["ARGENT_MESH_SECRET"] = secret
        (d / "secret").write_text(secret)  # remembered for this node's CLI calls
        # Each fake node logs to the fleet dir, and must not scribble on the
        # real ~/.argent activity feed.
        env["HOME"] = str(d)
        self.procs[node_id] = subprocess.Popen(
            [sys.executable, "-m", "argent_utils.mesh"],
            cwd=LINUX_DIR, env=env,
            stdout=(d / "node.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        self.dirs[node_id] = d

    def state(self, node_id: str) -> dict:
        try:
            return json.loads((self.dirs[node_id] / "state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def cli(self, node_id: str, *args: str, timeout: float = 30.0,
            secret: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(_proto_env())
        env["ARGENT_MESH_DIR"] = str(self.dirs[node_id])
        env["HOME"] = str(self.dirs[node_id])
        env["ARGENT_MESH_SECRET"] = (
            secret if secret is not None
            else (self.dirs[node_id] / "secret").read_text()
        )
        return subprocess.run(
            [sys.executable, "-m", "argent_utils.mesh", *args],
            cwd=LINUX_DIR, env=env, capture_output=True, text=True, timeout=timeout,
        )

    def kill(self, node_id: str) -> None:
        proc = self.procs.pop(node_id, None)
        if proc:
            proc.kill()
            proc.wait(timeout=10)

    def stop_all(self) -> None:
        for node_id in list(self.procs):
            self.kill(node_id)


def _wait_for(predicate, timeout: float = 15.0, interval: float = 0.2, what: str = ""):
    """Poll until the predicate returns a truthy value; fail loudly with its
    last value otherwise (network tests must never hang silently)."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    pytest.fail(f"timed out waiting for {what or predicate} (last: {last!r})")


@pytest.fixture()
def fleet(tmp_path):
    f = Fleet(tmp_path)
    yield f
    f.stop_all()


def _links_up(state: dict, expect_peers: int) -> bool:
    peers = state.get("peers", [])
    return len([p for p in peers if p.get("link") == "up"]) >= expect_peers


def _assignments(state: dict) -> dict:
    return {k: tuple(v.get("assigned", []))
            for k, v in (state.get("assignments") or {}).items()}


def test_mesh_discovery_assignment_failover_and_dispatch(fleet):
    """One flow, one fleet: cheaper than a fleet per assertion, and closer to
    the real lifecycle (a mesh lives through all of these in sequence)."""
    # The user's fleet: a Linux box + a strong and a weak MacBook.
    fleet.start("aaaa", "lin", "linux", tier=4)
    fleet.start("bbbb", "mac-strong", "macos", tier=1)
    fleet.start("cccc", "mac-weak", "macos", tier=4)

    # 1. Discovery: every node links to both others.
    for nid in ("aaaa", "bbbb", "cccc"):
        _wait_for(lambda nid=nid: _links_up(fleet.state(nid), 2),
                  what=f"{nid} to link 2 peers")

    # 2. Deterministic agreement: all three nodes publish identical assignments.
    def agreed():
        views = [_assignments(fleet.state(n)) for n in ("aaaa", "bbbb", "cccc")]
        return views[0] if (views[0] and views[0] == views[1] == views[2]) else None

    assignments = _wait_for(agreed, what="all nodes to agree on assignments")
    # weakest-first: grunt duties land on the weak machines, never the strong mac.
    assert assignments["review"] == ("aaaa",)
    assert assignments["conflicts"] == ("aaaa",)
    assert assignments["audit"] == ("aaaa", "cccc")  # one linux + one (weak) macos

    # 3. Dispatch: the audit spreads onto exactly the two assigned machines.
    r = fleet.cli("aaaa", "--dispatch", "audit", "--prompt", "bundle e2e please")
    assert r.returncode == 0, r.stdout + r.stderr
    spawned = fleet.root / "spawned"
    assert (spawned / "lin.txt").read_text() == "bundle e2e please"
    assert (spawned / "mac-weak.txt").read_text() == "bundle e2e please"
    assert not (spawned / "mac-strong.txt").exists()

    # 4. Token failover at dispatch time: the weak mac runs out of tokens →
    #    the macos slot fails over to the strong mac (edited REMOTELY from lin).
    r = fleet.cli("aaaa", "--set", "tokens=out", "--node", "cccc")
    assert r.returncode == 0, r.stdout + r.stderr
    _wait_for(
        lambda: _assignments(fleet.state("aaaa")).get("audit") == ("aaaa", "bbbb"),
        what="audit's macos slot to fail over to mac-strong",
    )
    r = fleet.cli("aaaa", "--dispatch", "audit", "--prompt", "second run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (spawned / "mac-strong.txt").read_text() == "second run"

    # 5. LWW override gossip: flip review to strongest-first on ONE node; every
    #    node converges on the same new owner.
    r = fleet.cli("bbbb", "--set", "tokens=ok", "--node", "cccc")  # restore first
    assert r.returncode == 0
    import socket as _socket  # override edit goes through the ctl protocol

    env_dir = fleet.dirs["bbbb"]
    port = json.loads((env_dir / "state.json").read_text())["tcpPort"]
    with _socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        f = sock.makefile("rwb")
        f.write(b'{"t":"ctl","v":1}\n')
        f.write(json.dumps({
            "t": "set-overrides", "duty": "review",
            "placement": {"strategy": "strongest-first", "tokenAware": True, "spread": []},
        }).encode() + b"\n")
        f.flush()
        assert json.loads(f.readline())["t"] == "ok"
    for nid in ("aaaa", "bbbb", "cccc"):
        _wait_for(
            lambda nid=nid: _assignments(fleet.state(nid)).get("review") == ("bbbb",),
            what=f"{nid} to adopt the strongest-first override",
        )

    # 6. Failover on death: kill the weak mac; both survivors move the audit's
    #    macos slot to the strong mac and mark the peer down.
    fleet.kill("cccc")
    for nid in ("aaaa", "bbbb"):
        _wait_for(
            lambda nid=nid: _assignments(fleet.state(nid)).get("audit") == ("aaaa", "bbbb"),
            what=f"{nid} to reassign the audit after mac-weak died",
        )
    down = [p for p in fleet.state("aaaa")["peers"] if p["id"] == "cccc"]
    assert down and down[0]["link"] == "down"

    # 7. The takeover is visible in each survivor's activity feed (HOME is the
    #    node dir, so the shared audit.jsonl lands inside the fixture).
    feed = (fleet.dirs["aaaa"] / ".argent" / "pr-monitor" / "audit.jsonl").read_text()
    assert "mesh-takeover" in feed and "mesh-peer-down" in feed


def test_secret_fences_peers_and_control(fleet):
    """With ARGENT_MESH_SECRET set, a wrong-secret node never links (it can
    beacon all it wants) and a wrong-secret CLI can't drive the node."""
    fleet.start("aaaa", "lin", "linux", tier=4, secret="hunter2")
    fleet.start("bbbb", "mac", "macos", tier=1, secret="hunter2")
    fleet.start("dddd", "intruder", "linux", tier=1, secret="wrong")
    _wait_for(lambda: _links_up(fleet.state("aaaa"), 1), what="secret peers to link")

    # Give the intruder ample beacon rounds, then confirm nobody linked it.
    time.sleep(2.0)
    assert not any(p.get("link") == "up" for p in fleet.state("aaaa").get("peers", [])
                   if p.get("id") == "dddd")
    assert not any(p.get("link") == "up" for p in fleet.state("dddd").get("peers", []))
    # Grunt duties stay inside the fenced mesh, never on the intruder.
    assert _assignments(fleet.state("aaaa"))["review"] == ("aaaa",)

    # Control sessions honor the same fence.
    r = fleet.cli("aaaa", "--status", secret="wrong")
    assert "not answering" in (r.stdout + r.stderr)
    r = fleet.cli("aaaa", "--set", "tokens=low", secret="hunter2")
    assert r.returncode == 0


def test_node_restart_is_a_new_incarnation(fleet):
    fleet.start("aaaa", "lin", "linux", tier=4)
    fleet.start("bbbb", "mac", "macos", tier=1)
    _wait_for(lambda: _links_up(fleet.state("aaaa"), 1), what="initial link")

    # Restart the mac; the linux node must re-link with the NEW process
    # (epoch bump) rather than trusting the dead link.
    fleet.kill("bbbb")
    fleet.start("bbbb", "mac", "macos", tier=1)
    _wait_for(
        lambda: _links_up(fleet.state("aaaa"), 1)
        and _links_up(fleet.state("bbbb"), 1),
        what="re-link after restart",
    )
    # And the restarted node's view must converge to agreement again.
    _wait_for(
        lambda: _assignments(fleet.state("aaaa")) == _assignments(fleet.state("bbbb"))
        and _assignments(fleet.state("aaaa")),
        what="post-restart assignment agreement",
    )
