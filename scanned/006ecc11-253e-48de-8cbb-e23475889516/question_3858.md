# Q3858: fork boundary applied at different heights via `pre_dispatch_tx_hook` (hooks_impl.rs)

## Question
Can an unprivileged attacker who sends transactions that maximise the state diff a commitment must carry, controlling which JMT keys are read and written, drive `pre_dispatch_tx_hook` in `crates/citrea-stf/src/hooks_impl.rs` so that the fork the native node applies at height N and the fork the circuit applies stop being the same, breaking the invariant that fork activation is a pure function of height?

## Target
- File/function: `crates/citrea-stf/src/hooks_impl.rs` -> `pre_dispatch_tx_hook`
- Entrypoint: unprivileged party sends transactions that maximise the state diff a commitment must carry
- Attacker controls: which JMT keys are read and written
- Exploit idea: fork boundary applied at different heights - reach `pre_dispatch_tx_hook` from that entrypoint and force the divergence where the fork the native node applies at height N and the fork the circuit applies stop being the same; the adjacent symbols in the same file that carry the value are `post_dispatch_tx_hook`, `begin_l2_block_hook`, `end_l2_block_hook`, `begin_slot_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fork activation is a pure function of height
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: execute a boundary block both ways and diff
