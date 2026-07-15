"""Pure self-tests for the tester's own reference codec + placement oracle.

These need no candidate: they prove the *tester itself* implements the spec's
codec round-trips (V2), freshness/LWW ordering (V3) and placement vectors (V1)
correctly, so that when it judges a candidate it is judging against a trustworthy
oracle. Run with ``--selftest``. If these fail, fix the tester before trusting
any candidate verdict.
"""

from __future__ import annotations

from . import assign, codec
from .codec import NodeInfo
from .model import load_model
from .report import Reporter


def run(rep: Reporter) -> None:
    model = load_model()
    _codec_roundtrips(rep)
    _decode_dropset(rep)
    _freshness(rep)
    _placement_vectors(rep, model)
    _permutation_invariance(rep, model)


def _codec_roundtrips(rep: Reporter) -> None:
    rep.begin_case("S1", "Codec round-trips every message type (V2)")
    info = NodeInfo(id="a" * 32, name="n", platform="linux", tier=4, tokens="ok",
                    tcp_port=40878, epoch=1000.0, seq=3, sees=("b" * 32,),
                    duties_enabled={"audit": False})
    back = NodeInfo.from_dict(info.to_dict())
    rep.check("NodeInfo encode→decode is identity", back == info, "MUST", "04-messages#nodeinfo")
    for name, msg in [
        ("beacon", codec.beacon(info)),
        ("hello", codec.hello(info, {"rev": 0, "updatedBy": "", "duties": {}})),
        ("heartbeat", codec.heartbeat()),
        ("node", codec.node_update(info)),
    ]:
        decoded = codec.decode(codec.encode(msg))
        rep.check(f"{name} encode→decode preserves the object",
                  decoded is not None and decoded.get("t") == msg["t"], "MUST",
                  "03-transport#framing")


def _decode_dropset(rep: Reporter) -> None:
    rep.begin_case("S2", "decode() drops exactly the malformed set (V2)")
    cases = {
        "empty": b"",
        "non-JSON": b"{not json}\n",
        "JSON array (non-object)": b"[1,2,3]\n",
        "object without string t": b'{"x":1}\n',
        "invalid UTF-8": b"\xff\xfe\n",
        "over 512 KiB": b'{"t":"x","p":"' + b"a" * (512 * 1024) + b'"}\n',
    }
    for label, raw in cases.items():
        rep.check(f"drops: {label}", codec.decode(raw) is None, "MUST", "03-transport#framing")
    rep.check("accepts a valid object", codec.decode(b'{"t":"heartbeat"}\n') is not None,
              "MUST", "03-transport#framing")
    rep.check("NodeInfo without id is invalid",
              NodeInfo.from_dict({"name": "x"}) is None, "MUST", "04-messages#nodeinfo")
    rep.check("NodeInfo with non-numeric tier is invalid",
              NodeInfo.from_dict({"id": "x", "tier": "abc"}) is None, "MUST",
              "04-messages#nodeinfo")
    only_id = NodeInfo.from_dict({"id": "x"})
    rep.check("NodeInfo with only id fills defaults",
              only_id is not None and only_id.tokens == "ok" and only_id.tier == 3, "MUST",
              "04-messages#nodeinfo")


def _freshness(rep: Reporter) -> None:
    rep.begin_case("S3", "Freshness ordering: epoch dominates seq (V3)")
    a = NodeInfo(id="x", epoch=200.0, seq=1)
    b = NodeInfo(id="x", epoch=100.0, seq=50)
    rep.check("(epoch=200,seq=1) supersedes (epoch=100,seq=50)", a.newer_than(b), "MUST",
              "04-messages#nodeinfo")
    c = NodeInfo(id="x", epoch=100.0, seq=51)
    rep.check("within an epoch, higher seq wins", c.newer_than(b), "MUST", "04-messages#nodeinfo")


def _placement_vectors(rep: Reporter, model) -> None:
    rep.begin_case("S4", "Placement oracle matches the spec vectors (V1)")
    A = NodeInfo(id="a" * 32, platform="linux", tier=4, tokens="ok")
    B = NodeInfo(id="b" * 32, platform="macos", tier=1, tokens="ok")
    C = NodeInfo(id="c" * 32, platform="macos", tier=4, tokens="ok")
    fleet = [A, B, C]

    def assigned(duty, nodes, overrides=None):
        return tuple(assign.assign_duty(model, duty, nodes, overrides, local_id=A.id)["assigned"])

    rep.check("review → [A]", assigned("review", fleet) == (A.id,), "MUST", "10-conformance")
    rep.check("conflicts → [A]", assigned("conflicts", fleet) == (A.id,), "MUST", "10-conformance")
    rep.check("audit → [A, C]", assigned("audit", fleet) == (A.id, C.id), "MUST", "10-conformance")
    ov = {"rev": 1, "updatedBy": "z", "duties": {
        "review": {"strategy": "strongest-first", "tokenAware": True, "spread": []}}}
    rep.check("review strongest-first → [B]", assigned("review", fleet, ov) == (B.id,), "MUST",
              "10-conformance")
    only_bc = [B, C]
    a = assign.assign_duty(model, "audit", only_bc, local_id=A.id)
    rep.check("audit with {B,C} → [C], linux shortfall",
              tuple(a["assigned"]) == (C.id,) and a["shortfall"] == [{"platform": "linux", "missing": 1}],
              "MUST", "10-conformance")
    A_out = NodeInfo(id=A.id, platform="linux", tier=4, tokens="out")
    rep.check("review, A tokens=out → [C]", assigned("review", [A_out, B, C]) == (C.id,), "MUST",
              "10-conformance")
    A_low = NodeInfo(id=A.id, platform="linux", tier=4, tokens="low")
    rep.check("review, A tokens=low → [C]", assigned("review", [A_low, B, C]) == (C.id,), "MUST",
              "10-conformance")
    empty = assign.assign_duty(model, "review", [], local_id=A.id)
    rep.check("empty fleet → [], unsatisfied", empty["assigned"] == [] and empty["shortfall"],
              "MUST", "10-conformance")


def _permutation_invariance(rep: Reporter, model) -> None:
    rep.begin_case("S5", "Placement is permutation-invariant (V1)")
    A = NodeInfo(id="a" * 32, platform="linux", tier=4, tokens="ok")
    B = NodeInfo(id="b" * 32, platform="macos", tier=1, tokens="ok")
    C = NodeInfo(id="c" * 32, platform="macos", tier=4, tokens="ok")
    import itertools
    base = assign.assign_all(model, [A, B, C], local_id=A.id)
    all_same = all(
        assign.assign_all(model, list(perm), local_id=A.id) == base
        for perm in itertools.permutations([A, B, C]))
    rep.check("shuffling the input order never changes any assignment", all_same, "MUST",
              "06-coordination#determinism-requirements")
