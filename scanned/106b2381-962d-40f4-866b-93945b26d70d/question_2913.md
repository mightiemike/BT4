# Q2913: update_ema amplifies a cheap input into expensive work (stream_throttle.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `update_ema` in `streamer/src/nonblocking/stream_throttle.rs` with a repeated operation that the code assumes happens at most once, and make a minimal accepted input to `update_ema` fan out into disproportionate downstream work, so that the invariant "Work performed is proportional to the size and fee of the input that triggered it." breaks and the result is DoS?

## Target
- File/function: `streamer/src/nonblocking/stream_throttle.rs` -> `update_ema()` (around line 103)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: a repeated operation that the code assumes happens at most once
- Exploit idea: Send the smallest accepted input that makes `update_ema` fan out into large downstream work, so a cheap transaction/packet costs the node orders more.
- Invariant to test: Work performed is proportional to the size and fee of the input that triggered it.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Plot input bytes versus work done in `update_ema`; assert the ratio is bounded by a constant.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work and exhaust node memory or disk below true transaction cost.
