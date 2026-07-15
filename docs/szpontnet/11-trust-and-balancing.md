# 11 - Trust levels & surplus load balancing

Chapters 01-10 specify the SzpontNet **core**: discovery, links, gossip,
leaderless assignment, dispatch. This chapter specifies the layer built on top of
it - **who a node trusts** and **how a dispatcher chooses where work goes**. Both
are **additive** (a node that advertises no `pubkey` and no `stats`, and configures
no allowlist, behaves exactly as the core describes), so a v1 core node and a node
implementing this chapter interoperate on the same mesh.

A dispatched unit of work is called a **SzpontRequest** throughout this chapter;
on the wire it is still a [`dispatch`](04-messages.md#dispatch) message carrying a
[Job](04-messages.md#job). The name is the user-facing one - "run this for me,
whoever's best placed."

## Two trust levels

Trust exists because a *personal* SzpontRequest runs **directly** on the receiver -
staging a prompt and spawning work that can take **social actions under your
identity** (submitting a PR, commenting on GitHub via your CLI). Granting that to
the wrong peer is a privilege-escalation bug. From any node's point of view a peer
is one of two levels:

| Level | Meaning | What happens to its SzpontRequests |
|-------|---------|-----------------------------------|
| **personal** | a device *you have explicitly trusted* | run **directly**, as if you had triggered the work from your own panel |
| **foreign** | any other device | **declined** in v1 (see [the foreign path](#the-foreign-path-future-zero-trust)) |

### Trust is never derived from an advertisement

**Assume every advertised field is spoofed.** A node's `id`, `name`, and any other
self-reported value are display-only and **grant zero privilege** - a stranger can
beacon any of them. Trust therefore rests on two things a stranger cannot forge:

1. **A proven device key.** Each node has an Ed25519 keypair
   ([08](08-state.md#devicekey)); its **fingerprint** is `sha256(public key)`. The
   public key is advertised (as [`pubkey`](04-messages.md#nodeinfo)), but
   *advertising it grants nothing*. On every link the peer must **prove possession**
   of the matching private key: our [`hello`](04-messages.md#hello) carries a fresh
   random `nonce`, and the peer must return an [`auth`](04-messages.md#auth)
   message signing that nonce. Only a peer holding the private key can produce a
   valid signature, and the nonce is per-connection so a captured signature can't
   be replayed. A peer that copies someone else's advertised `pubkey` cannot sign
   our challenge for it, so it is never *verified* as that identity.
2. **A local allowlist.** Trust is **set manually by the operator and stored only
   on this machine** ([`trusted.json`](08-state.md#trustedjson), never gossiped): a
   set of fingerprints marked as "my devices."

The executor classifies the requester from the **verified fingerprint of the link
the request arrived on** - never from the job's self-reported `requestedBy`:

```
function classify(verified_fingerprint, allowlist) -> "personal" | "foreign":
    if allowlist is empty:                          # boundary not configured
        return "personal"                           # full trust (v1-compatible)
    if verified_fingerprint in allowlist:
        return "personal"
    return "foreign"                                # unlisted, or never verified
```

**Empty allowlist = full trust**, so a fresh mesh behaves exactly like the
pre-trust core. The moment you trust even one device the boundary switches on and
every unlisted (or unverified) peer becomes foreign. Enabling zero-trust is thus a
deliberate act: `--trust <fingerprint>` (get a peer's fingerprint from its
`--fingerprint`, shown in `--status`, or its `state.json`).

> Because verification is symmetric and per-link, an unverified peer (an old core
> node with no key, or a lib-less keyless node) has **no** verified fingerprint, so
> `classify` returns foreign under any non-empty allowlist. That is the correct,
> conservative outcome: you never grant personal access to something you couldn't
> authenticate.

### The personal path (v1)

When a personal peer sends a SzpontRequest, the receiving node **runs it
directly** - exactly the [execution](07-dispatch.md#execution) the core describes:
stage the prompt, spawn the work, reply `spawned`. There is no extra hop. A
review SzpontRequest from your laptop runs on your desktop just as if you had
pressed the review button there yourself - full-trust altruism, scoped to the
devices you have explicitly trusted.

### The foreign path (future zero-trust)

The foreign path is **deliberately unimplemented in v1.** A node that receives a
SzpontRequest from a foreign device **declines** it (a [`declined`](#refusals-are-first-class)
`job-status`, reason `"foreign device (zero-trust path not implemented)"`).

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

- **MUST NOT** derive trust from any advertised field. Trust rests only on a
  verified key fingerprint against a local allowlist.
- **MUST** treat an **empty** allowlist as full trust (`personal` for all), so a
  fresh mesh interoperates with core-only nodes; and treat a peer that has **not**
  proved a key as having no fingerprint, hence `foreign` under any non-empty
  allowlist.
- **MUST** verify proof of possession before treating a peer as `personal`: the
  peer's [`auth`](04-messages.md#auth) signature over *our* fresh per-connection
  `nonce` must validate against the `pubkey` it advertised. It **MUST** classify
  the requester from that verified link identity, never from `requestedBy`.
- **MUST** omit `pubkey` and `stats` from an advertisement when they are empty, so
  a node that uses neither is byte-compatible with a core v1 advertisement.
- **MUST** treat a `declined` `job-status` as a non-`spawned` outcome (fail the
  slot over), exactly like `failed`, whether or not it understands the reason.
- **SHOULD** decline a foreign device's SzpontRequest until the zero-trust foreign
  path is implemented, rather than running it.
- **SHOULD** rank dispatch targets `surplus-first` and MUST fall back to
  weakest-first ordering when surpluses tie (including the all-neutral case).
- **MAY** advertise `stats`; a node that doesn't is treated as surplus 0 and ranks
  by the core strategies, never as an error.

Everything here rides `v: 1` and the [compatibility contract](09-extensibility.md#the-compatibility-contract):
new optional fields (`pubkey`, `stats`), new message types (`auth`), a new enum
value (`declined`), a new strategy id (`surplus-first`), all safe to add without a
version bump.
