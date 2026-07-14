# 07 — Dispatch

Assignment ([06](06-coordination.md)) decides *who should* run a duty. **Dispatch**
is the act of actually running one now: staging a job, routing it to the chosen
node(s), and failing over if a chosen node can't take it. Dispatch is optional for
a node that only wants to *offer* resources — but any node that wants to *originate*
work implements it, and any node that accepts work implements the receiving half
([execution](#execution)).

## Jobs

A dispatch carries a [Job](04-messages.md#job): an `id`, the `duty`, an opaque
`prompt` (the work payload), and provenance (`requestedBy`, `requestedAt`). The
dispatcher assigns a fresh unique `id` per job.

## Slots

A duty's placement may require **spread** across platforms ([06](06-coordination.md#placement-policy)) —
e.g. the `audit` duty runs on *one Linux and one macOS node*. Dispatch therefore
runs **one job per slot**:

- A **no-spread** duty has a single slot labeled `"any"`.
- A **spread** duty has one slot per unit of `{platform, count}` (a `count: 2`
  contributes two slots for that platform).

Each slot has its own **ranked candidate list**, computed by `slot_candidates`:

```
function slot_candidates(duty, live_nodes, overrides, local_id) -> [(slot_label, [node_id])]:
    policy = effective_placement(duty, overrides)
    ranked = sort(eligible(live_nodes, duty, policy), strategy_key(policy.strategy, local_id))
    if policy.spread is empty:
        return [("any", [n.id for n in ranked])]              # one slot, all eligible nodes
    slots = []
    for (platform, count) in policy.spread:
        of_platform = [n.id for n in ranked if n.platform == platform]
        repeat count times: slots.append((platform, of_platform))
    return slots
```

The candidate list for a slot is the assigned node **first**, then every other
eligible node of that platform by rank — so a slot survives its top pick dropping
out between gossip rounds.

## Routing a job

To dispatch a duty, a node:

1. computes `slot_candidates` over the current live set;
2. for each slot in order, walks its candidate list and tries to place the job on
   the first candidate that accepts, **skipping any node already used by an
   earlier slot** (so two slots never land on one machine);
3. records one result per slot: `{slot, node, nodeName, status, reason}`.

```
used = {}
results = []
for (slot_label, candidates) in slot_candidates(...):
    outcome = {slot: slot_label, node: null, status: "failed", reason: "no eligible node"}
    for node_id in candidates:
        if node_id in used: continue
        (status, reason) = place_on(node_id, duty, prompt)     # local or remote
        if status == "spawned":
            used.add(node_id)
            outcome = {slot: slot_label, node: node_id, status: "spawned"}
            break
        outcome = {slot: slot_label, node: node_id, status: "failed", reason: reason}
    results.append(outcome)
return results
```

A slot whose every candidate declines (or that has no candidates) ends `failed` —
the dispatch as a whole is "partial" but the other slots still ran. This is the same
shape whether a candidate declines because it is **dead**, **out of tokens**, or —
under a [future altruism limit](09-extensibility.md#the-altruism-limits-roadmap) —
**over quota**: any non-`spawned` outcome simply advances to the next candidate.

## Placing on a node

- **Local** (`node_id` is this node): run the job here directly
  ([execution](#execution)) and use its `spawned`/`failed` result.
- **Remote**: send a [`dispatch`](04-messages.md#dispatch) on that peer's link and
  wait for the [`job-status`](04-messages.md#job-status) reply, up to
  `dispatchAckTimeoutSecs` (default **8 s**). Map the reply's `status`/`reason` to
  the slot outcome; a timeout or link error is a `failed` outcome
  (`reason: "peer did not answer"`) and the slot fails over.

  A dispatcher correlates the reply to the request by Job `id`; it MUST tolerate
  (drop) a `job-status` for an unknown id.

## Execution

The **receiving half** of dispatch. A node that receives a
[`dispatch`](04-messages.md#dispatch) — on an
[authenticated](03-transport.md#the-join-fence) link, or from a control client —
**runs the job locally** and replies with a [`job-status`](04-messages.md#job-status):

- On success (the work was started): `status: "spawned"`.
- On failure (the node could not start it — e.g. no way to launch it here):
  `status: "failed"` with a human `reason`; the dispatcher fails the slot over to
  the next candidate.

What "run locally" *means* is implementation-defined and outside the wire protocol.
Argent Mesh stages the `prompt` to a file and opens a terminal running an agent on
it, exactly like a local spawn; a headless deployment substitutes its own runner
(the reference honors an `ARGENT_MESH_SPAWN` command template for exactly this).
SzpontNet only requires that the node truthfully report `spawned` vs `failed`.

> **v1 reports hand-off, not completion.** `spawned` means the node accepted and
> started the work; SzpontNet does not track the job to completion or return its
> result. Completion tracking is a [reserved extension](09-extensibility.md).

## Dispatching via a control session

A UI or CLI dispatches by opening a [control session](04-messages.md#control-messages)
to its **local** node and sending a [`dispatch`](04-messages.md#dispatch); the node
performs the routing above and replies with a
[`dispatch-result`](04-messages.md#dispatch-result) carrying the per-slot outcomes.
This is how the topology panel's "run on mesh" and the CLI's `--dispatch` work: the
client talks only to its local node, which does the mesh routing on its behalf.

## Idempotency & duplicates

SzpontNet does not deduplicate jobs: a `dispatch` is a fire-once request, and job
`id`s are unique per dispatch. If the same logical work is dispatched twice (two
operators, or a retried dispatch), two jobs run. A dispatcher SHOULD avoid
re-dispatching the same work; a receiver treats every `dispatch` it accepts as new.
Exactly-once semantics are out of scope for v1.
