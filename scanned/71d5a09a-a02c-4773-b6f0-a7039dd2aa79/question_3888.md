# Q3888: prev-hash chaining via `post_dispatch_tx_hook` (hooks_impl.rs)

## Question
Can an unprivileged attacker who sends transactions crafted so the native witness and the guest replay diverge, controlling the fork-activation height it targets, drive `post_dispatch_tx_hook` in `crates/citrea-stf/src/hooks_impl.rs` so that the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal, breaking the invariant that L2 blocks form a hash chain with no forks?

## Target
- File/function: `crates/citrea-stf/src/hooks_impl.rs` -> `post_dispatch_tx_hook`
- Entrypoint: unprivileged party sends transactions crafted so the native witness and the guest replay diverge
- Attacker controls: the fork-activation height it targets
- Exploit idea: prev-hash chaining - reach `post_dispatch_tx_hook` from that entrypoint and force the divergence where the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal; the adjacent symbols in the same file that carry the value are `pre_dispatch_tx_hook`, `begin_l2_block_hook`, `end_l2_block_hook`, `begin_slot_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L2 blocks form a hash chain with no forks
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert a block with a mismatched parent and assert rejection
