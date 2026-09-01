# Q0690: short header proof requested versus stored via `get_finalized_block` (da.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling the L2 height at which its transactions land, drive `get_finalized_block` in `crates/sequencer/src/da.rs` so that the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact, breaking the invariant that every queried L1 hash has a matching stored proof?

## Target
- File/function: `crates/sequencer/src/da.rs` -> `get_finalized_block`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: short header proof requested versus stored - reach `get_finalized_block` from that entrypoint and force the divergence where the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact; the adjacent symbols in the same file that carry the value are `da_block_monitor`, `fee_rate_monitor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every queried L1 hash has a matching stored proof
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: query an unstored hash and assert a defined outcome
