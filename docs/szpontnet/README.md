# SzpontNet — a LAN peer-to-peer resource-sharing protocol

**Version 1 (`v: 1`).** This directory is the normative specification for
**SzpontNet**: a small, leaderless protocol that lets the machines on a local
network find each other, **advertise the resources they have available**, and
hand work to whichever machine is the best fit — with no central coordinator and
automatic take-over when a machine drops.

SzpontNet is the *protocol*. **Argent Mesh** (in this repository under
[`linux/argent_utils/mesh/`](../../linux/argent_utils/mesh)) is its reference
implementation; the shared constants live in
[`core/mesh.json`](../../core/mesh.json). This spec is written so that a second,
independent implementation — in any language — can join the same mesh and
interoperate byte-for-byte with the reference one.

> The name is deliberately informal (Polish *szpont/spont* — "on a whim",
> impromptu): you power a machine on, it spontaneously joins, offers what it has,
> and takes on work. No registration, no server, no config beyond an optional
> shared secret.

---

## What it does, in one paragraph

Every participating machine runs a **node**. A node **beacons** its presence over
UDP (multicast + broadcast). Nodes that hear each other open a single **TCP link**
per pair and exchange a **hello** carrying that node's *resource advertisement* —
what platform it is, how strong a machine it is (its *tier*), how much budget it
has left (its *token* state), and which classes of work (*duties*) it is willing
to run. Nodes gossip these advertisements so every node holds the same view of the
mesh. Because every node runs the **same deterministic placement function** over
that shared view, they all agree — with no election — on which machine owns each
duty; when a machine dies or runs dry, every survivor has *already* recomputed and
the work has moved. Any node can then **dispatch** a job, and the mesh routes it to
the chosen machine(s), failing over if the first pick can't take it.

---

## Design goals

1. **Zero configuration.** A node self-discovers peers and self-assigns work.
   The only optional knob is a shared secret to fence off who may join.
2. **Leaderless and self-healing.** No coordinator to elect or lose. Placement is
   a pure function of the gossiped state, so all nodes converge on the same answer
   and failover is automatic.
3. **Resource-advertisement first.** A node's whole purpose on the wire is to say
   *"here is what I can do"*; the protocol is the machinery that turns those
   advertisements into placement decisions.
4. **Full trust / altruism (v1).** Every node offers its resources freely and
   accepts any job for a duty it has enabled. There is no accounting, payment, or
   admission policy beyond eligibility. This is a deliberate v1 simplification.
5. **Extensible without breaking changes.** The trust model, the resource
   vocabulary, the duty catalog and the placement strategies are all designed to
   grow — in particular so that **limits on altruism** (quotas, caps, priorities,
   accounting) can be added later without any v1 node needing to change. The rules
   that make this safe are normative; see [09-extensibility](09-extensibility.md).
6. **Tolerant.** Unknown fields are ignored, unknown message types are dropped,
   malformed input is never fatal. A newer node must never wedge an older one.

---

## The trust model (v1): full altruism

SzpontNet v1 assumes the LAN is **cooperative and trusted**. Concretely:

- Any node may join the mesh (or any node presenting the shared secret, if one is
  configured — see [03-transport](03-transport.md#the-join-fence)).
- A node **advertises its resources honestly** and **accepts any dispatched job**
  for a duty it has locally enabled and is eligible for. It does not weigh cost,
  fairness, or its own load beyond the coarse `tokens` signal.
- There is **no reservation, no accounting, no admission control**. Placement is
  advisory-by-consensus: every node computes the same owner, and dispatch honors
  it, but nothing *enforces* that only the owner runs a job.

This is intentional. The whole point of v1 is to be small enough to implement in
an afternoon. Everything needed to later constrain that altruism — per-peer
quotas, max-concurrent-jobs, priority classes, cost accounting, accept/reject
policies — is reserved as additive extension points and specified in
[09-extensibility](09-extensibility.md#the-altruism-limits-roadmap), so a future
`v: 2` (or even a capability-negotiated v1 extension) can add them without
breaking the wire compatibility described here.

---

## How to read this spec

The chapters are ordered so you can implement bottom-up:

| # | Chapter | What you implement from it |
|---|---------|----------------------------|
| — | [README](README.md) (this file) | the mental model, goals, trust model |
| 01 | [Model & terminology](01-model.md) | the nouns: node, advertisement, duty, placement, dispatch |
| 02 | [Discovery](02-discovery.md) | UDP beacons, the multicast/broadcast pair, the "smaller id dials" rule |
| 03 | [Transport & security](03-transport.md) | TCP links, NDJSON framing, the link state machine, the join fence |
| 04 | [Message reference](04-messages.md) | every message type, field by field, with JSON schemas |
| 05 | [Resource advertisement](05-resources.md) | what a node advertises and how the vocabulary extends |
| 06 | [Coordination & assignment](06-coordination.md) | the deterministic placement algorithm (with pseudocode) |
| 07 | [Dispatch](07-dispatch.md) | routing a job, slot fan-out, failover, results |
| 08 | [State & persistence](08-state.md) | `node.json`, `state.json`, liveness, incarnations |
| 09 | [Extensibility & future work](09-extensibility.md) | the compatibility rules + the altruism-limits roadmap |
| 10 | [Conformance](10-conformance.md) | MUST/SHOULD/MAY, a minimal-node checklist, interop vectors |
| A | [Appendix A — annotated trace](appendix-a-trace.md) | a full two-node session, message by message |
| B | [Appendix B — constants](appendix-b-constants.md) | every default value in one table |

### Notation

- The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, **MAY** are
  used as in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119): they mark
  interoperability requirements, not implementation advice.
- Wire examples are JSON. On the wire each message is a single line of compact
  JSON (no interior newlines) terminated by `\n` — see
  [03-transport](03-transport.md#framing). Examples here are shown pretty-printed
  for readability; the newline-free encoding is what actually travels.
- `int`, `float`, `string`, `bool`, `object`, `array` refer to JSON types.
- Field names are given exactly as they appear on the wire (they are
  case-sensitive).

### The fastest path to a working node

A minimal conformant node needs, in order: a UDP beacon sender + listener
([02](02-discovery.md)), a TCP listener + dialer speaking NDJSON
([03](03-transport.md)), the `beacon`/`hello`/`node`/`heartbeat` messages
([04](04-messages.md)), the resource advertisement it puts in its hello
([05](05-resources.md)), and the placement function ([06](06-coordination.md)).
Dispatch ([07](07-dispatch.md)) and the control endpoint are optional for a node
that only wants to *offer* resources. The exact minimal set is enumerated in
[10-conformance](10-conformance.md#minimal-node).

---

## Relationship to the reference implementation

Everything in this spec is implemented and exercised by Argent Mesh:

- Wire protocol & node: [`linux/argent_utils/mesh/`](../../linux/argent_utils/mesh)
  (`protocol.py`, `node.py`, `assign.py`, `identity.py`, `statefile.py`, `ctl.py`).
- Shared constants & vocabulary: [`core/mesh.json`](../../core/mesh.json).
- Interop-relevant behavior is covered by
  [`linux/tests/test_mesh_logic.py`](../../linux/tests/test_mesh_logic.py) (the
  placement function) and
  [`linux/tests/test_mesh_node.py`](../../linux/tests/test_mesh_node.py) (real
  multi-node sockets: discovery, gossip, failover, the join fence).

Where this spec and the reference implementation disagree, that is a bug in one
of them; please report it. The spec is the interoperability contract.
