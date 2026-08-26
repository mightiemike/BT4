# Q5007: bank::update_epoch_stakes - epoch stakes computed from a mutable snapshot (resizing a large account in the)

## Question
Can an unprivileged attacker who submits transactions that are loaded, executed, committed and hashed into the bank, resizing a large account in the same block that the bank freezes, drive `bank::update_epoch_stakes` to make update_epoch_stakes observe stake state that a later transaction in the same block changes, so that the invariant that epoch stakes are computed from a fixed snapshot at the epoch boundary is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank.rs` -> `update_epoch_stakes`
- Entrypoint: submits transactions that are loaded, executed, committed and hashed into the bank, resizing a large account in the same block that the bank freezes
- Attacker controls: transaction contents, account state it owns, submission timing across slot and epoch boundaries, and fork placement
- Exploit idea: Make update_epoch_stakes observe stake state that a later transaction in the same block changes.
- Invariant to test: Epoch stakes are computed from a fixed snapshot at the epoch boundary.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank integration test replaying the crafted transaction sequence and asserting capitalization, bank hash and accounting are stable
