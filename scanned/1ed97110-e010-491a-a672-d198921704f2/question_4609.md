# Q4609: commitment index continuity via `mod` (mod.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling the L2 height at which its transactions land, drive `mod` in `crates/sequencer/src/commitment/mod.rs` so that the commitment index the sequencer emits and the index the light client expects next stop being consecutive, breaking the invariant that commitment indices form a gapless chain?

## Target
- File/function: `crates/sequencer/src/commitment/mod.rs` -> `mod`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment index continuity - reach `mod` from that entrypoint and force the divergence where the commitment index the sequencer emits and the index the light client expects next stop being consecutive; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment indices form a gapless chain
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: emit around a gap and assert the light client refuses to advance
