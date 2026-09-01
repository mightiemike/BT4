# Q1719: state diff accumulation via `end_l2_block` (lib.rs)

## Question
Can an unprivileged attacker who sends transactions that maximise the state diff a commitment must carry, controlling which JMT keys are read and written, drive `end_l2_block` in `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs` so that the state diff the circuit outputs and the diff the DA blob carries stop being the same diff, breaking the invariant that published diffs equal proved diffs?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs` -> `end_l2_block`
- Entrypoint: unprivileged party sends transactions that maximise the state diff a commitment must carry
- Attacker controls: which JMT keys are read and written
- Exploit idea: state diff accumulation - reach `end_l2_block` from that entrypoint and force the divergence where the state diff the circuit outputs and the diff the DA blob carries stop being the same diff; the adjacent symbols in the same file that carry the value are `RuntimeTxHook`, `Runtime`, `GenesisParams`, `ApplySequencerCommitmentsOutput`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: published diffs equal proved diffs
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: diff the published blob against the circuit output
