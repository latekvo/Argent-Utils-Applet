"""A multi-identity probe mesh: the tester's fleet of well-behaved fake nodes.

To exercise a candidate's placement, gossip, dispatch and fence behavior
black-box, the tester needs to *be* the rest of the mesh around it. ``ProbeMesh``
runs one or more ``ProbePeer`` identities — each a spec-correct SzpontNet node
(beacon, hello handshake, heartbeats, gossip, dispatch executor) — over real
sockets, so from the candidate's point of view it is talking to genuine peers.

Each peer also *records* everything it receives and exposes hooks
(``send``, ``gossip_self``, ``raw_accept_handler``) so a test can drive precise
scenarios: retune an advertisement, inject an override, or play the adversary in
the outbound-dial fence test.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from dataclasses import replace

from . import codec, net
from .codec import Job, NodeInfo


def wait_until(predicate, timeout: float, interval: float = 0.1):
    """Poll ``predicate`` until truthy; return its value or None on timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last if last else None


class ProbePeer:
    """One fake node identity the tester presents to the candidate."""

    def __init__(
        self, mesh: "ProbeMesh", id: str, name: str, platform: str, tier: int,
        tokens: str = "ok", duties_enabled: dict | None = None,
        dispatch_reply: str = "spawned", dial_mode: str = "auto",
        raw_accept_handler=None,
    ) -> None:
        self.mesh = mesh
        self.dispatch_reply = dispatch_reply  # "spawned" | "failed" | "silent"
        self.dial_mode = dial_mode            # "auto" (id rule) | "always" | "never"
        self.raw_accept_handler = raw_accept_handler  # callable(conn, peer) for adversary tests

        # Listen socket the candidate dials when candidate.id < peer.id.
        host = "127.0.0.1" if mesh.loopback else "0.0.0.0"
        self.listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen.bind((host, 0))
        self.listen.listen(8)
        self.listen.settimeout(0.3)
        self.tcp_port = self.listen.getsockname()[1]

        self.info = NodeInfo(
            id=id, name=name, platform=platform, tier=tier, tokens=tokens,
            tcp_port=self.tcp_port, duties_enabled=duties_enabled or {},
            epoch=mesh.epoch, seq=0,
        )

        self._lock = threading.Lock()          # guards socket sends + info mutation
        self._conn: socket.socket | None = None
        self.linked = False
        self.accept_count = 0                  # inbound dials the candidate made to us
        self.received: list[dict] = []         # parsed messages seen on the link
        self.raw_received: list[bytes] = []    # raw frames, for framing checks
        self.jobs: list[Job] = []              # dispatch jobs the candidate sent us
        self.overrides: dict = {"rev": 0, "updatedBy": "", "duties": {}}
        self._stop = False
        self._dialing = False
        self._frozen = False   # simulate a silent death (stop sending heartbeats)

    # MARK: - beacon payload

    def beacon_bytes(self) -> bytes:
        with self._lock:
            return codec.encode(codec.beacon(self.info))

    # MARK: - link setup (both directions)

    def _accept_loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.listen.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.accept_count += 1
            if self.raw_accept_handler is not None:
                threading.Thread(target=self._raw_serve, args=(conn,), daemon=True).start()
                continue
            threading.Thread(target=self._serve_accepted, args=(conn,), daemon=True).start()

    def _raw_serve(self, conn: socket.socket) -> None:
        try:
            self.raw_accept_handler(conn, self)
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _serve_accepted(self, conn: socket.socket) -> None:
        """Candidate dialed us: it sends its hello first; we reply with ours."""
        reader = net.LineReader(conn)
        first = reader.read_line(timeout=10.0)
        if not first:
            conn.close()
            return
        msg = codec.decode(first)
        if not msg or msg.get("t") != "hello":
            conn.close()
            return
        self._record(first, msg)
        self._send_raw(conn, codec.encode(
            codec.hello(self.info, self.overrides, self.mesh.secret)))
        self._run_link(conn, reader)

    def _dial_candidate(self) -> None:
        with self._lock:
            if self.linked or self._dialing:
                return
            self._dialing = True
        try:
            cand = self.mesh.candidate
            if not cand or not cand.get("addr") or not cand.get("tcp_port"):
                return
            try:
                conn = net.connect_tcp(cand["addr"], cand["tcp_port"], timeout=5.0)
            except OSError:
                return
            self._send_raw(conn, codec.encode(               # dialer sends hello first
                codec.hello(self.info, self.overrides, self.mesh.secret)))
            self._run_link(conn, net.LineReader(conn))
        finally:
            with self._lock:
                self._dialing = False

    def _run_link(self, conn: socket.socket, reader: net.LineReader) -> None:
        with self._lock:
            self._conn = conn
            self.linked = True
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        try:
            while not self._stop:
                line = reader.read_line(timeout=1.0)
                if line is None:
                    if reader.closed:
                        break
                    continue
                msg = codec.decode(line)
                self._record(line, msg)
                if msg is not None:
                    self._handle(conn, msg)
        finally:
            with self._lock:
                self.linked = False
                self._conn = None
            with contextlib.suppress(OSError):
                conn.close()

    def freeze(self) -> None:
        """Stop sending heartbeats without closing the socket — simulates a
        silent death so the candidate must reap us via the heartbeat timeout
        (03-transport link-state), not a clean EOF."""
        self._frozen = True

    def _heartbeat_loop(self) -> None:
        interval = self.mesh.proto["heartbeatIntervalSecs"]
        while not self._stop:
            time.sleep(interval)
            if self._frozen:
                continue
            conn = self._conn
            if conn is None:
                return
            try:
                self._send_raw(conn, codec.encode(codec.heartbeat()))
            except OSError:
                return

    # MARK: - message handling (executor half + gossip sink)

    def _handle(self, conn: socket.socket, msg: dict) -> None:
        t = msg.get("t")
        if t == "dispatch":
            job = Job.from_dict(msg.get("job") or {})
            if job is None:
                return
            self.jobs.append(job)
            if self.dispatch_reply == "silent":
                return
            reason = "" if self.dispatch_reply == "spawned" else "probe declined (test)"
            self._send_raw(conn, codec.encode(codec.job_status(
                job.id, self.dispatch_reply, reason, self.info.id)))
        elif t == "overrides":
            raw = msg.get("overrides")
            if isinstance(raw, dict):
                self.overrides = raw

    # MARK: - test-driver hooks

    def send(self, msg: dict) -> bool:
        conn = self._conn
        if conn is None:
            return False
        try:
            self._send_raw(conn, codec.encode(msg))
            return True
        except OSError:
            return False

    def gossip_self(self, **changes) -> bool:
        """Bump our advertisement (seq+1, applying ``changes``) and gossip it."""
        with self._lock:
            self.info = self.info.bumped(**changes)
            info = self.info
        return self.send(codec.node_update(info))

    def set_info(self, **changes) -> None:
        with self._lock:
            self.info = replace(self.info, **changes)

    def _send_raw(self, conn: socket.socket, data: bytes) -> None:
        with self._lock:
            conn.sendall(data)

    def _record(self, raw: bytes, msg: dict | None) -> None:
        with self._lock:
            self.raw_received.append(raw)
            if msg is not None:
                self.received.append(msg)

    def messages(self, t: str | None = None) -> list[dict]:
        with self._lock:
            msgs = list(self.received)
        return [m for m in msgs if m.get("t") == t] if t else msgs

    def stop(self) -> None:
        self._stop = True
        with contextlib.suppress(OSError):
            self.listen.close()
        conn = self._conn
        if conn is not None:
            with contextlib.suppress(OSError):
                conn.close()


class ProbeMesh:
    """Owns the shared discovery sockets and a set of ProbePeer identities."""

    def __init__(self, model, proto: dict, candidate_id: str, loopback: bool,
                 secret: str = "") -> None:
        self.model = model
        self.proto = proto
        self.candidate_id = candidate_id
        self.loopback = loopback
        self.secret = secret
        self.epoch = time.time()
        self.group = proto["multicastGroup"]
        self.mport = proto["multicastPort"]
        self.peers: list[ProbePeer] = []
        self.candidate: dict = {}              # learned from the candidate's beacons
        self.candidate_beacons: list[dict] = []
        self.candidate_beacon_raw: list[bytes] = []
        self._rx = net.make_beacon_rx(self.group, self.mport, loopback)
        self._tx = net.make_beacon_tx(loopback)
        self._stop = False

    def add_peer(self, **kwargs) -> ProbePeer:
        peer = ProbePeer(self, **kwargs)
        self.peers.append(peer)
        return peer

    def start(self) -> None:
        for peer in self.peers:
            threading.Thread(target=peer._accept_loop, daemon=True).start()
        threading.Thread(target=self._beacon_loop, daemon=True).start()
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._dial_loop, daemon=True).start()

    def _beacon_loop(self) -> None:
        interval = self.proto["beaconIntervalSecs"]
        while not self._stop:
            for peer in self.peers:
                net.send_beacon(self._tx, peer.beacon_bytes(), self.group, self.mport, self.loopback)
            time.sleep(interval)

    def _rx_loop(self) -> None:
        while not self._stop:
            try:
                self._rx.settimeout(0.3)
                data, (host, _) = self._rx.recvfrom(4096)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                return
            msg = codec.decode(data)
            if not msg or msg.get("t") != "beacon":
                continue
            if str(msg.get("id")) == self.candidate_id:
                raw = data if data.endswith(b"\n") else data + b"\n"
                self.candidate_beacons.append(msg)
                self.candidate_beacon_raw.append(raw)
                addr = "127.0.0.1" if self.loopback else host
                self.candidate = {
                    "id": self.candidate_id, "addr": addr,
                    "tcp_port": msg.get("tcpPort"), "epoch": msg.get("epoch"),
                    "name": msg.get("name"), "platform": msg.get("platform"),
                }

    def _dial_loop(self) -> None:
        """Once the candidate is known, each peer whose id sorts below it dials
        (smaller-id-dials); the rest wait to be dialed on their listen sockets."""
        while not self._stop:
            if self.candidate.get("tcp_port"):
                for peer in self.peers:
                    if peer.linked or peer._dialing:
                        continue
                    should_dial = (
                        peer.dial_mode == "always"
                        or (peer.dial_mode == "auto" and peer.info.id < self.candidate_id)
                    )
                    if should_dial:
                        threading.Thread(target=peer._dial_candidate, daemon=True).start()
            time.sleep(0.25)

    def raw_beacon(self, payload: dict) -> None:
        """Send an arbitrary beacon (adversarial / spoof tests)."""
        net.send_beacon(self._tx, codec.encode(payload), self.group, self.mport, self.loopback)

    def stop(self) -> None:
        self._stop = True
        for peer in self.peers:
            peer.stop()
        with contextlib.suppress(OSError):
            self._rx.close()
        with contextlib.suppress(OSError):
            self._tx.close()
