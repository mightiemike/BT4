# Q5466: JMT version bookkeeping via `revert` (scratchpad.rs)

## Question
Can an unprivileged attacker who sends an L2 transaction whose execution touches a JMT slot no other transaction touches, controlling intra-block ordering of its own transactions, drive `revert` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` so that the storage version a write is recorded under and the version the root is computed for stop being the same, breaking the invariant that each root corresponds to exactly one version?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` -> `revert`
- Entrypoint: unprivileged party sends an L2 transaction whose execution touches a JMT slot no other transaction touches
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: JMT version bookkeeping - reach `revert` from that entrypoint and force the divergence where the storage version a write is recorded under and the version the root is computed for stop being the same; the adjacent symbols in the same file that carry the value are `StateReaderAndWriter`, `StateDelta`, `AccessoryDelta`, `OffchainDelta`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each root corresponds to exactly one version
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: interleave versions and diff computed roots
