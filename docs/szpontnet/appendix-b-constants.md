# Appendix B — Constants

Every default value SzpontNet v1 nodes must agree on, in one place. The canonical
source is [`core/mesh.json`](../../core/mesh.json); these are its v1 values. Two
nodes that disagree on the discovery group/ports, or whose timing values differ far
enough, will not form a healthy mesh; nodes that disagree on the *vocabulary*
(platforms/tiers/tokens/duties/strategies) still interoperate at the wire level but
may place work differently ([09](09-extensibility.md#vocabulary-skew)).

## Protocol

| Constant | Value | Used in |
|----------|-------|---------|
| protocol `version` / message `v` | `1` | every message |
| `multicastGroup` | `239.83.77.7` | [discovery](02-discovery.md) |
| `multicastPort` | `40877` | [discovery](02-discovery.md) |
| `tcpPortBase` | `40878` | [transport binding](03-transport.md#binding) |
| `tcpPortSpan` | `10` (ports `40878`–`40887`) | [transport binding](03-transport.md#binding) |
| `beaconIntervalSecs` | `2.0` | [beacons](02-discovery.md#beacons) |
| `heartbeatIntervalSecs` | `2.0` | [heartbeats](03-transport.md#link-state) |
| `peerStaleSecs` | `5.0` | [link state → `stale`](03-transport.md#link-state) |
| `peerTimeoutSecs` | `10.0` | [link state → `down`](03-transport.md#link-state) |
| `dispatchAckTimeoutSecs` | `8.0` | [remote dispatch wait](07-dispatch.md#placing-on-a-node) |
| `stateWriteIntervalSecs` | `2.0` | [snapshot write cadence](08-state.md#statejson--the-snapshot) |
| `MAX_LINE_BYTES` | `524288` (512 KiB) | [framing](03-transport.md#framing) |
| UDP receive buffer | ≥ `2048` bytes | [discovery receive](02-discovery.md#receiving) |
| multicast TTL | `1` (link-local) | [discovery send](02-discovery.md#transport-multicast-plus-broadcast) |
| down-peer retention | `300` s (reference) | [snapshot retention](08-state.md#down-peer-retention) |

> Timing values are the reference defaults. An implementation MAY expose overrides
> for testing (the reference reads `ARGENT_MESH_*` env vars to run fast-timed
> meshes on loopback), but nodes on the *same* mesh must use compatible values —
> in particular `peerTimeoutSecs` must exceed `heartbeatIntervalSecs` with margin,
> and `peerStaleSecs` must sit between them.

## Tiers

| Constant | Value |
|----------|-------|
| `tiers.min` | `1` (strongest) |
| `tiers.max` | `5` (weakest) |
| `tiers.default` | `3` |

Tier is clamped to `[min, max]` on apply ([04](04-messages.md#set-attr)).

## Tokens

| Value | Rank | Placement effect |
|-------|------|------------------|
| `ok` | `0` | preferred |
| `low` | `1` | eligible, de-prioritized behind `ok` |
| `out` | `2` | excluded from token-aware duties |
| (any other) | `1` | treated like `low` — never excluded ([09 rule 3](09-extensibility.md#the-compatibility-contract)) |

## Platforms (v1 vocabulary)

| id | notes |
|----|-------|
| `linux` | |
| `macos` | |

Platforms carry display metadata (`emoji`, `linuxGlyph`, `colorHex`) that is
presentation only. Unknown platforms are opaque strings.

## Strategies

| id | ranking (after token rank) |
|----|----------------------------|
| `weakest-first` (default, and the unknown-strategy fallback) | prefer larger `tier` (weaker machine) |
| `strongest-first` | prefer smaller `tier` (stronger machine) |
| `local-first` | prefer the dispatching node, then weakest-first |

`defaultStrategy` = `weakest-first`. Full sort keys in
[06-coordination](06-coordination.md#ranking).

## Duties (v1 vocabulary)

| id | default placement |
|----|-------------------|
| `review` | `{strategy: weakest-first, tokenAware: true, spread: []}` |
| `conflicts` | `{strategy: weakest-first, tokenAware: true, spread: []}` |
| `audit` | `{strategy: weakest-first, tokenAware: true, spread: [{linux,1},{macos,1}]}` |

## Message types

`beacon`, `hello`, `node`, `overrides`, `heartbeat`, `set-attr`, `dispatch`,
`job-status`, `ctl`, `status`, `state`, `set-overrides`, `stop`, `ok`, `error`,
`dispatch-result`. Full reference: [04-messages](04-messages.md).

## Job statuses

| Value | Meaning |
|-------|---------|
| `spawned` | node accepted and started the work |
| `failed` | node could not start it (dispatcher fails the slot over) |

(`rejected`, `completed`, … are [reserved extensions](09-extensibility.md#adding-a-job-status).)

## Reference file locations

| Path | Contents |
|------|----------|
| `~/.argent/mesh/node.json` | persisted identity + attributes ([08](08-state.md#nodejson)) |
| `~/.argent/mesh/state.json` | public topology snapshot ([08](08-state.md#statejson--the-snapshot)) |
| overridable via | `ARGENT_MESH_DIR` |
| join secret via | `ARGENT_MESH_SECRET` ([03](03-transport.md#the-join-fence)) |
