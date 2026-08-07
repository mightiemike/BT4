# Q3749: snapshot_config settles one authorization twice (snapshot_controller.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `snapshot_config` in `runtime/src/snapshot_controller.rs` with a repeated operation that the code assumes happens at most once, and have `snapshot_config` apply the same authorized effect a second time, so that the invariant "One signed authorization produces exactly one state effect." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime/src/snapshot_controller.rs` -> `snapshot_config()` (around line 49)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a repeated operation that the code assumes happens at most once
- Exploit idea: Get `snapshot_config` to apply the same logical effect twice from a single user authorization by re-entering it or replaying the surrounding flow.
- Invariant to test: One signed authorization produces exactly one state effect.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Integration test: submit the flow twice (and once re-entrantly) and assert the second application is rejected and balances moved once.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can cause account state to be silently lost, duplicated, or resurrected across cache flush, shrink, ancient-append-vec packing, clean, or purge, changing user balances without a transaction.
