# 11 - Trust levels & surplus load balancing

Chapters 01-10 specify the SzpontNet **core**: discovery, links, gossip,
leaderless assignment, dispatch. This chapter specifies the layer built on top of
it - **who a node trusts** and **how a dispatcher chooses where work goes**. Both
are **additive** (a node that advertises no `owner` and no `stats` behaves exactly
as the core describes), so a v1 core node and a node implementing this chapter
interoperate on the same mesh.

A dispatched unit of work is called a **SzpontRequest** throughout this chapter;
on the wire it is still a [`dispatch`](04-messages.md#dispatch) message carrying a
[Job](04-messages.md#job). The name is the user-facing one - "run this for me,
whoever's best placed."

## Two trust levels

Every node belongs to an **owner** - a stable id for whoever runs it (a person, a
fleet). It rides on the advertisement as the optional
[`owner`](04-messages.md#nodeinfo) field. From any node's point of view, a peer is
one of two trust levels:

| Level | Meaning | What happens to its SzpontRequests |
|-------|---------|-----------------------------------|
| **personal** | one of *my own* devices (same owner) | run **directly**, as if I had triggered the work from my own panel |
| **foreign** | someone *else's* device (a different owner) | **declined** in v1 (see [the foreign path](#the-foreign-path-future-zero-trust)) |

### Classifying a peer

Trust is decided by the **executor** (the node that receives a SzpontRequest),
comparing the requester's advertised `owner` to its own:

```
function trust_of(peer_owner, my_owner) -> "personal" | "foreign":
    if my_owner == "" or peer_owner == "":   # either side unset - can't decide
        return trust.default                 # "personal"
    return "personal" if peer_owner == my_owner else "foreign"
```

The key rule: **if either owner is unset, the peer is personal.** This is the
v1-compatible default (`trust.default = "personal"`). A mesh where nobody has set
an owner is therefore fully trusting - identical to the pre-trust core. Foreign
only ever arises when *both* the requester and the executor have set owners and
they differ. This makes the whole feature opt-in: you get zero-trust behavior
only once you actually label your devices.

An `owner` is set like any other attribute - a [`set-attr`](04-messages.md#set-attr)
(`owner=my-fleet`), the `ARGENT_MESH_OWNER` environment variable (so a fleet can
stamp the same owner on every machine without editing each `node.json`), or
persisted in [`node.json`](08-state.md#nodejson).

### The personal path (v1)

When a personal peer sends a SzpontRequest, the receiving node **runs it
directly** - exactly the [execution](07-dispatch.md#execution) the core describes:
stage the prompt, spawn the work, reply `spawned`. There is no extra hop. A
review SzpontRequest from your laptop runs on your desktop just as if you had
pressed the review button there yourself. This is the full-trust altruism of the
core, now scoped to "my own fleet."

### The foreign path (future zero-trust)

The foreign path is **deliberately unimplemented in v1.** A node that receives a
SzpontRequest from a foreign owner **declines** it (a [`declined`](#refusals-are-first-class)
`job-status`, reason `"foreign requester (zero-trust path not implemented)"`).

The intended future design (reserved, not yet normative): a foreign node MAY run
the *compute* half of a SzpontRequest, but any **social action** - submitting a
pull request, commenting on GitHub, anything that acts under an identity - MUST be
sent **back to a personal node of the requester** to perform, rather than executed
locally. That keeps a stranger's machine from ever acting as you. Until that
routing exists, declining is the safe behavior, and it costs nothing: the
dispatcher's [failover](07-dispatch.md#routing-a-job) already handles a declined
candidate like any other, so a foreign node simply falls out of consideration.

## Per-node stats: account-aware load balancing

The core ranks nodes by tier and the coarse `tokens` signal. This chapter adds a
finer, **budget-aware** ranking so a dispatcher can send work to whoever actually
has spare capacity. Each node tracks two quantities locally and advertises them in
the additive [`stats`](04-messages.md#nodeinfo) object
(`{"plan", "usageAvg", "quotaLeft"}`):

### usageAvg - a 21-day rolling average

`usageAvg` is an **exponentially-weighted rolling average of token usage**, in
capacity units per day, with a ~21-day time constant. It is a decaying reservoir:
each unit of usage adds to `acc`; `acc` decays as `acc *= exp(-Δdays / τ)` with
`τ = usageTimeConstantDays` (21); the advertised average is `acc / τ`. A node that
consumes at a steady rate `r` settles at `usageAvg = r`; a node that goes idle sees
its average decay by `1/e` each time constant. This is the node's *typical burn*.

### quotaLeft - account-type aware

`quotaLeft` is the **remaining capacity in the current quota window**. Capacity is
`plan.weight × capacityPerWeight`, where the plan weight encodes the subscription
tier **relative to Pro**:

| Plan | `weight` | Relative capacity |
|------|----------|-------------------|
| `pro` | 1 | 1x |
| `max-5x` | 5 | 5x |
| `max-20x` | 20 | 20x |

So a Max 20x node has 4x the room of a Max 5x node. The window rolls every
`quotaWindowDays` (7), resetting what's been used. **Absolute token quotas are
deliberately not modelled** - Anthropic's real limits are dynamic rolling windows,
so hard-coding token counts would be brittle and wrong. Everything is compared in
these plan-relative units, which is enough to rank *comparative* headroom
correctly.

> **Where the numbers come from.** The reference node books `jobCostUnits` of
> usage each time it spawns a SzpontRequest, and exposes `set-attr` keys
> (`plan`, `quotaLeft`, `usageAvg`, `usage`) to inject or correct the accounting.
> Wiring `usageAvg`/`quotaLeft` to a first-party view of real Claude usage is a
> follow-up; the *mechanism* - track, advertise, rank, decline - is what this
> chapter specifies, and it degrades safely when the inputs are neutral.

### Surplus

A node's **surplus** is the single number the load balancer ranks on:

```
surplus(node) = quotaLeft - usageAvg        # in plan-relative units; 0 if no stats
```

It is the spare quota a node has *after* covering its own typical burn. A node that
advertises no `stats` has surplus **0** (neutral). The advertised `stats` values
decay locally over time so an idle node's displayed surplus ages, but a node
re-gossips only on a real change (a spawn, an edit), not every tick, so idle
accounting does not churn the mesh.

## Choosing a target is the dispatcher's call - no consensus

The core's [assignment](06-coordination.md) is a *consensus* computation: every
node computes the same duty owner, and that drives the **displayed** ownership in
the panel. **Dispatch target selection is separate and unilateral.** When a node
dispatches a SzpontRequest, it ranks candidates by `dispatchStrategy`
(**`surplus-first`** by default) over *its own* gossiped view and picks - with no
agreement from anyone. Two consequences:

- **Load balancing follows surplus, not the displayed owner.** The panel may show
  a duty owned by the weakest machine (stable, weakest-first), while a live
  dispatch of that duty lands on a different machine that currently has the most
  spare quota. That is intentional: fast-moving budget shifts load without
  churning the stable ownership view. See
  [placement vs dispatch strategy](06-coordination.md#placement-strategy-vs-dispatch-strategy).
- **A dispatcher may target whoever it likes.** It can name an explicit
  [`target`](07-dispatch.md#routing-a-job) and send the request there directly,
  with no failover - *"Alice may forward everything to Bob, even if Bob is low."*
  The receiver is free to refuse.

`surplus-first` ranks by descending surplus, tie-breaking with the same
`(tokens, tier, id)` order as weakest-first - so when no node advertises stats (all
surplus 0), it degrades **exactly** to weakest-first and the core behavior is
preserved. See the [ranking table](06-coordination.md#ranking).

## Refusals are first-class

Because a dispatcher chooses unilaterally, the **receiver must be able to say no.**
A node replies with a [`declined`](04-messages.md#job-status) `job-status` (distinct
from `failed`) when it refuses a SzpontRequest for policy. The v1 reference declines
when:

- the requester is **foreign** (the zero-trust path above);
- the duty is **disabled** locally (`dutiesEnabled[duty] == false` - the node opted
  out of that class of work);
- the node is **out of tokens** (`tokens == "out"` - it cannot serve; *this is Bob
  refusing the job Alice sent him anyway*).

A `declined` outcome is handled by the *exact same failover* that handles a dead or
out-of-budget candidate: any non-`spawned` status advances the slot to the next
candidate ([07](07-dispatch.md#routing-a-job)). An explicit `target` is the one
exception - it has a single candidate, so a decline there is reported as-is, no
failover (the dispatcher chose that node on purpose).

## Conformance

An implementation of this chapter:

- **MUST** default an unset-owner comparison to `personal` (rule: either side
  unset ⇒ personal), so an owner-less mesh stays fully trusting and interoperates
  with core-only nodes.
- **MUST** omit `owner` and `stats` from an advertisement when they are empty, so
  a node that uses neither is byte-compatible with a core v1 advertisement.
- **MUST** treat a `declined` `job-status` as a non-`spawned` outcome (fail the
  slot over), exactly like `failed`, whether or not it understands the reason.
- **SHOULD** decline a foreign requester's SzpontRequest until the zero-trust
  foreign path is implemented, rather than running it.
- **SHOULD** rank dispatch targets `surplus-first` and MUST fall back to
  weakest-first ordering when surpluses tie (including the all-neutral case).
- **MAY** advertise `stats`; a node that doesn't is treated as surplus 0 and ranks
  by the core strategies, never as an error.

Everything here rides `v: 1` and the [compatibility contract](09-extensibility.md#the-compatibility-contract):
new optional fields, a new enum value (`declined`), a new strategy id
(`surplus-first`), all safe to add without a version bump.
