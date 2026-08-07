# Q2781: remove_neighbor can be driven into unbounded work (route.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `remove_neighbor` in `xdp/src/route.rs` with an interleaving where the write lands between the read and the validation, and make `remove_neighbor` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `remove_neighbor` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `xdp/src/route.rs` -> `remove_neighbor()` (around line 403)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: an interleaving where the write lands between the read and the validation
- Exploit idea: Grow the attacker-controlled collection `remove_neighbor` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `remove_neighbor` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `remove_neighbor` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can trigger a panic, unwrap, assertion, index corruption, or unrecoverable I/O error inside AccountsDB, the bucket map, or snapshot handling and halt validators.
