# 04 — Message reference

Every SzpontNet message is a JSON object with a string **type** field `t` and an
integer **version** field `v` (default `1`), encoded as one newline-terminated
line ([03-transport](03-transport.md#framing)). This chapter is the exhaustive
catalog. Unless stated otherwise, a receiver **MUST** ignore fields it does not
recognize and **MUST NOT** fail on a message whose optional fields are absent.

Transport legend: **UDP** = sent as a discovery datagram; **link** = sent on a
peer TCP link; **ctl** = sent on a control session (client↔node).

| `t` | transport | direction | purpose |
|-----|-----------|-----------|---------|
| [`beacon`](#beacon) | UDP | broadcast | "I exist, dial me here" |
| [`hello`](#hello) | link | both, first message | full advertisement + overrides; opens a peer link |
| [`node`](#node) | link | gossip | an updated advertisement |
| [`overrides`](#overrides) | link | gossip | updated placement overrides (LWW) |
| [`heartbeat`](#heartbeat) | link | both | liveness keep-alive |
| [`set-attr`](#set-attr) | link / ctl | to a node | change a node's advertised attributes |
| [`dispatch`](#dispatch) | link / ctl | to a node | run a job here |
| [`job-status`](#job-status) | link | reply | outcome of a dispatch |
| [`ctl`](#ctl) | ctl | client→node, first message | opens a control session |
| [`status`](#status) | ctl | client→node | request the state snapshot |
| [`state`](#state) | ctl | node→client | the state snapshot (reply to `status`) |
| [`set-overrides`](#set-overrides) | ctl | client→node | edit a duty's placement policy |
| [`stop`](#stop) | ctl | client→node | ask the node to shut down |
| [`ok` / `error`](#ok--error) | ctl | node→client | generic command results |
| [`dispatch-result`](#dispatch-result) | ctl | node→client | per-slot dispatch outcomes |

Two composite objects recur inside messages and are defined first:
[**NodeInfo**](#nodeinfo) (the resource advertisement) and [**Job**](#job).

---

## Composite objects

### NodeInfo

The resource advertisement for one node. Appears inside `hello` and `node`, and
(decorated with link fields) inside the [`state`](#state) snapshot.

```json
{
  "id": "3236817363144d8dbd842ec2973506c2",
  "name": "softoobox",
  "platform": "linux",
  "tier": 4,
  "tokens": "ok",
  "tcpPort": 40878,
  "epoch": 1784057237.23,
  "seq": 12,
  "sees": ["bd4eaf7671d24b9792bcfd09762ac5b5"],
  "dutiesEnabled": {"audit": false},
  "v": 1
}
```

| Field | Type | Req? | Meaning |
|-------|------|------|---------|
| `id` | string | **yes** | stable, mesh-unique node id. A NodeInfo without a usable `id` is invalid and MUST be dropped. |
| `name` | string | no (`"?"`) | human label; presentation only, never used for identity or placement. |
| `platform` | string | no (`"unknown"`) | machine kind (`"linux"`, `"macos"`, …); a *resource* — see [05](05-resources.md#platform). |
| `tier` | int | no (`3`) | machine strength, 1 = strongest — see [05](05-resources.md#tier). |
| `tokens` | string | no (`"ok"`) | budget availability: `"ok"`/`"low"`/`"out"` — see [05](05-resources.md#tokens). |
| `tcpPort` | int | no (`0`) | the node's TCP listen port. |
| `epoch` | float | no (`0`) | incarnation stamp; increases each process (re)start. |
| `seq` | int | no (`0`) | per-incarnation update counter. |
| `sees` | array<string> | no (`[]`) | ids of peers this node currently holds a link to (for topology display + partition awareness). |
| `dutiesEnabled` | object<string,bool> | no (`{}`) | per-duty opt-out; a duty absent from the map is **enabled** by default. |
| `v` | int | no (`1`) | protocol version of this advertisement. |

**Freshness.** Two NodeInfos for the same `id` are ordered by the tuple
`(epoch, seq)`: the larger wins. A restart (higher `epoch`) always supersedes the
prior incarnation regardless of `seq`; within one incarnation, the higher `seq`
is newer. Receivers MUST keep only the freshest NodeInfo per id and MUST NOT let
an older one overwrite a newer one. See [08-state](08-state.md#liveness--incarnations).

Validation: if `id` is missing, or a present numeric field fails to parse as its
type (e.g. `tier` is `"abc"`), the whole NodeInfo is invalid and MUST be dropped
(not partially applied).

### Job

A unit of dispatched work.

```json
{
  "id": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6",
  "duty": "audit",
  "prompt": "…the work payload, an opaque string…",
  "requestedBy": "3236817363144d8dbd842ec2973506c2",
  "requestedAt": 1784057240.5
}
```

| Field | Type | Req? | Meaning |
|-------|------|------|---------|
| `id` | string | **yes** | unique job id (dispatcher-assigned). |
| `duty` | string | **yes** | the duty this job belongs to. |
| `prompt` | string | no (`""`) | opaque work payload; SzpontNet does not interpret it. |
| `requestedBy` | string | no (`"?"`) | node id of the dispatcher. |
| `requestedAt` | float | no (now) | dispatcher's timestamp. |

A Job missing `id` or `duty` is invalid and MUST be dropped.

---

## Discovery message

### `beacon`

UDP presence advert. Small enough for one datagram; sent to the multicast group
and (off loopback) the subnet broadcast — see [02-discovery](02-discovery.md).

```json
{"t": "beacon", "id": "3236…", "name": "softoobox",
 "platform": "linux", "tcpPort": 40878, "epoch": 1784057237.23, "v": 1}
```

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | sender's node id. |
| `name` | string | sender's name. |
| `platform` | string | sender's platform. |
| `tcpPort` | int | **the port to dial for a link.** Receiver MUST ignore the beacon if missing/≤0. |
| `epoch` | float | sender's incarnation; a higher value than a linked peer's means it restarted. |

The beacon intentionally omits `tier`/`tokens`/`dutiesEnabled` — the authoritative
advertisement travels in the [`hello`](#hello), keeping beacons tiny.

---

## Peer-link messages

### `hello`

First message on a peer link, sent by **both** sides (the dialer sends it on
connect; the accepter sends it in reply). Carries the sender's full advertisement,
its current placement overrides, and — if a [join fence](03-transport.md#the-join-fence)
is configured — the shared secret.

```json
{"t": "hello",
 "node": { …NodeInfo… },
 "overrides": { …PlacementOverrides, see 06… },
 "secret": "optional-shared-secret",
 "v": 1}
```

| Field | Type | Req? | Meaning |
|-------|------|------|---------|
| `node` | NodeInfo | **yes** | the sender's advertisement. |
| `overrides` | object | no (`{}`) | the sender's placement overrides ([06](06-coordination.md#placement-overrides)); merged LWW. |
| `secret` | string | conditional | present iff a join fence is configured; MUST match. |

On receiving a valid `hello` a node: validates the secret; records/updates the
peer's NodeInfo (by freshness); binds this link's writer to that peer; merges the
`overrides`; and recomputes assignments. See
[03-transport](03-transport.md#the-join-fence) for the authentication ordering an
unauthenticated link MUST enforce before accepting anything other than a hello.

### `node`

A gossiped advertisement update — the sender relaying a (possibly other node's)
fresher NodeInfo across the mesh.

```json
{"t": "node", "node": { …NodeInfo… }, "v": 1}
```

Receiver merges the `node` by [freshness](#nodeinfo): adopt it only if newer than
what is held for that `id`; if adopted, re-propagate to other peers and recompute
assignments. A `node` for the receiver's own `id` is ignored.

### `overrides`

A gossiped [placement-overrides](06-coordination.md#placement-overrides) update.

```json
{"t": "overrides", "overrides": {"rev": 3, "updatedBy": "3236…", "duties": { … }}, "v": 1}
```

Receiver adopts it only if it **wins** the last-writer-wins comparison against the
overrides it currently holds (higher `rev`, ties broken by `updatedBy`); if
adopted, re-propagate and recompute. See
[06-coordination](06-coordination.md#placement-overrides).

### `heartbeat`

Liveness keep-alive, sent on every link every `heartbeatIntervalSecs`.

```json
{"t": "heartbeat", "ts": 1784057241.0, "v": 1}
```

`ts` is the sender's timestamp (informational). Receiving *any* message refreshes
a peer's liveness, but heartbeats guarantee traffic on an otherwise idle link so
[link state](03-transport.md#link-state) stays `up`.

### `set-attr`

Ask a node to change its own advertised attributes. Used both peer→peer-forwarded
and from a [control session](#control-messages): a UI can edit *any* node's
attributes, and the request is forwarded over the mesh to the target.

```json
{"t": "set-attr", "target": "bd4eaf…", "attrs": {"tokens": "out", "tier": 2}, "v": 1}
```

| Field | Type | Meaning |
|-------|------|---------|
| `target` | string | node id to edit; `""`, `"self"`, or the local id all mean *this* node. |
| `attrs` | object | attributes to apply (see below). |

**Applying `attrs`** (a node applying it to *itself*): each recognized key is
validated and applied; unknown keys and invalid values are ignored (the sender may
be a newer or older peer).

| `attrs` key | Type | Effect |
|-------------|------|--------|
| `name` | string | set the node's name (trimmed; non-empty; reference caps length at 64). |
| `tier` | int | set the tier, **clamped** to the model's `[min, max]` ([05](05-resources.md#tier)). |
| `tokens` | string | set token state; ignored unless one of `"ok"`/`"low"`/`"out"`. |
| `dutiesEnabled` | object<string,bool> | merge per-duty enable flags. |

If `target` names a **peer** (not self), the receiver **forwards** the `set-attr`
over that peer's link (it does not apply it locally). A node that applies a change
MUST bump its `seq`, persist the new attributes, gossip the new NodeInfo, and
recompute assignments.

### `dispatch`

Ask a node to run work now. `dispatch` has **two shapes** depending on the
transport — they are distinct and MUST both be supported by their respective
receivers:

**On a peer link** — a fully-formed [Job](#job) to run *on the receiving node*:

```json
{"t": "dispatch", "job": { …Job… }, "v": 1}
```

The receiver runs the job locally ([07-dispatch](07-dispatch.md#execution)) and
replies with a [`job-status`](#job-status). On an unauthenticated link a bare
`dispatch` MUST be rejected per [the fence ordering rule](03-transport.md#the-join-fence).

**On a [control session](#control-messages)** — a request to *route* a job through
the mesh, carrying the `duty` and `prompt` as **top-level** fields (the node mints
the Job id and does the [slot routing](07-dispatch.md#routing-a-job) itself):

```json
{"t": "dispatch", "duty": "audit", "prompt": "…the work payload…", "v": 1}
```

The node replies with a [`dispatch-result`](#dispatch-result) (the per-slot
outcomes), not a `job-status`. An unknown `duty` yields an [`error`](#ok--error).

> The two shapes exist because a peer link dispatches *one job to this node*, while
> a control client asks *this node to place a job across the mesh on its behalf*.
> Don't wrap the control-session form's `duty`/`prompt` in a `job` object.

### `job-status`

The outcome of a `dispatch`, sent back to the dispatcher.

```json
{"t": "job-status", "id": "b1c2…", "status": "spawned", "reason": "", "node": "bd4eaf…", "v": 1}
```

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | the Job id this is answering. |
| `status` | string | `"spawned"` (the node started the work) or `"failed"`. |
| `reason` | string | human-readable failure detail when `status` = `"failed"`; else `""`. |
| `node` | string | the id of the node reporting (the executor). |

> v1 defines exactly two statuses: `spawned` and `failed`. `spawned` means the
> node *accepted and started* the work, not that the work *completed* — SzpontNet
> tracks placement and hand-off, not job completion. Additional statuses are a
> reserved extension ([09](09-extensibility.md)).

---

## Control messages

Control messages flow on a **control session**: a TCP connection a client opens
with a `ctl` first message instead of a `hello`. The node answers each command
with exactly one reply line.

### `ctl`

Opens a control session. If a [join fence](03-transport.md#the-join-fence) is
configured, MUST carry the matching `secret`.

```json
{"t": "ctl", "secret": "optional-shared-secret", "v": 1}
```

The node validates the secret and then reads commands until the client
disconnects.

### `status`

Request the node's live state snapshot.

```json
{"t": "status", "v": 1}
```

Reply: one [`state`](#state) message.

### `state`

The node's state snapshot, sent in reply to `status`. Its `state` field has the
same shape as the persisted [`state.json`](08-state.md#statejson--the-snapshot) — the whole
topology as this node sees it.

```json
{"t": "state", "state": {
   "tcpPort": 40878,
   "self": { …NodeInfo… },
   "peers": [ { …NodeInfo…, "link": "up", "addr": "192.168.1.21", "lastSeenSecsAgo": 1.2 } ],
   "assignments": {"review": {"duty": "review", "assigned": ["…"], "shortfall": []}},
   "overrides": {"rev": 0, "updatedBy": "", "duties": {}}
}, "v": 1}
```

See [08-state](08-state.md#statejson--the-snapshot) for the full snapshot schema.

### `set-overrides`

Edit one duty's [placement policy](06-coordination.md#placement-overrides)
mesh-wide. The node bumps the last-writer-wins `rev`, applies it, and gossips it.

```json
{"t": "set-overrides", "duty": "review",
 "placement": {"strategy": "strongest-first", "tokenAware": true, "spread": []}, "v": 1}
```

Reply: [`ok`](#ok--error), or [`error`](#ok--error) if `duty` is unknown or
`placement` is malformed.

### `stop`

Ask the node to shut down cleanly. Reply: [`ok`](#ok--error).

```json
{"t": "stop", "v": 1}
```

### `ok` / `error`

Generic command results.

```json
{"t": "ok", "v": 1}
{"t": "error", "reason": "unknown duty 'foo'", "v": 1}
```

### `dispatch-result`

Reply to a control-session [`dispatch`](#dispatch): the per-slot outcomes of
routing the job through the mesh.

```json
{"t": "dispatch-result", "duty": "audit", "results": [
   {"slot": "linux", "node": "3236…", "nodeName": "softoobox", "status": "spawned", "reason": ""},
   {"slot": "macos", "node": "bd4e…", "nodeName": "mbp-weak", "status": "spawned", "reason": ""}
], "v": 1}
```

Each entry reports one [slot](07-dispatch.md#slots): which platform slot it was
(`"any"` for a no-spread duty), which node took it (`node`/`nodeName`), and the
`status`/`reason`. See [07-dispatch](07-dispatch.md).

---

## Encoding rules (summary)

- One object per line, compact (no interior newlines), UTF-8, `\n`-terminated.
- Always include `t` (string) and `v` (int, default 1).
- Lines longer than `MAX_LINE_BYTES` (512 KiB) are dropped.
- Unknown `t` → drop the message, keep the link. Unknown fields → ignore them.
- A malformed line is never fatal to the link (except an over-length line, which
  MAY close it). See [09-extensibility](09-extensibility.md) for the full
  compatibility contract.
