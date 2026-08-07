# Q2512: get_not_unique_leader_tpus amplifies a cheap input into expensive work (tpu_info.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `get_not_unique_leader_tpus` in `send-transaction-service/src/tpu_info.rs` with a lookup whose result is cached and then invalidated by the attacker's own write, and make a minimal accepted input to `get_not_unique_leader_tpus` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `send-transaction-service/src/tpu_info.rs` -> `get_not_unique_leader_tpus()` (around line 23)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a lookup whose result is cached and then invalidated by the attacker's own write
- Exploit idea: Send the smallest accepted input that makes `get_not_unique_leader_tpus` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `get_not_unique_leader_tpus`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
