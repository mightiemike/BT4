# Q0022: basefee update rule via `calculate_next_block_base_fee` (basefee.rs)

## Question
Can an unprivileged attacker who fills consecutive blocks to drive the base fee to a boundary, controlling the fill pattern across blocks, drive `calculate_next_block_base_fee` in `crates/primitives/src/basefee.rs` so that the base fee the sequencer computes and the base fee the circuit recomputes stop being equal, breaking the invariant that base fee is a deterministic function of the parent header?

## Target
- File/function: `crates/primitives/src/basefee.rs` -> `calculate_next_block_base_fee`
- Entrypoint: unprivileged party fills consecutive blocks to drive the base fee to a boundary
- Attacker controls: the fill pattern across blocks
- Exploit idea: basefee update rule - reach `calculate_next_block_base_fee` from that entrypoint and force the divergence where the base fee the sequencer computes and the base fee the circuit recomputes stop being equal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: base fee is a deterministic function of the parent header
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute in the guest and diff
