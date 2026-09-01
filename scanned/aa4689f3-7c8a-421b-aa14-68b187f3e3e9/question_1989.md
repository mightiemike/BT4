# Q1989: fork boundary applied at different heights via `apply_l2_block` (lib.rs)

## Question
Can an unprivileged attacker who sends transactions crafted so the native witness and the guest replay diverge, controlling the fork-activation height it targets, drive `apply_l2_block` in `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs` so that the fork the native node applies at height N and the fork the circuit applies stop being the same, breaking the invariant that fork activation is a pure function of height?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs` -> `apply_l2_block`
- Entrypoint: unprivileged party sends transactions crafted so the native witness and the guest replay diverge
- Attacker controls: the fork-activation height it targets
- Exploit idea: fork boundary applied at different heights - reach `apply_l2_block` from that entrypoint and force the divergence where the fork the native node applies at height N and the fork the circuit applies stop being the same; the adjacent symbols in the same file that carry the value are `RuntimeTxHook`, `Runtime`, `GenesisParams`, `ApplySequencerCommitmentsOutput`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fork activation is a pure function of height
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: execute a boundary block both ways and diff
