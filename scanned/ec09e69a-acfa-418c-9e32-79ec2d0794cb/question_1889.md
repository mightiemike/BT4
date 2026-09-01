# Q1889: prev-hash chaining via `init_chain` (lib.rs)

## Question
Can an unprivileged attacker who sends transactions crafted so the native witness and the guest replay diverge, controlling the size and shape of the state diff, drive `init_chain` in `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs` so that the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal, breaking the invariant that L2 blocks form a hash chain with no forks?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs` -> `init_chain`
- Entrypoint: unprivileged party sends transactions crafted so the native witness and the guest replay diverge
- Attacker controls: the size and shape of the state diff
- Exploit idea: prev-hash chaining - reach `init_chain` from that entrypoint and force the divergence where the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal; the adjacent symbols in the same file that carry the value are `RuntimeTxHook`, `Runtime`, `GenesisParams`, `ApplySequencerCommitmentsOutput`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L2 blocks form a hash chain with no forks
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert a block with a mismatched parent and assert rejection
