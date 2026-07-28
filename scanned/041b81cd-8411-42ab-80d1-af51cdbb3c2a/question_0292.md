# Q0292: EVM resume height - topic binding double record

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `getStartBlock` remain safe if they control indexed topics for sender, recipient, tx hash, and log index, or can that make it create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop, violate the rule that reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:getStartBlock
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop
- Invariant to test: reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a fork or local EVM devnet and compare raw log bytes against the persisted `store.Event` row and the resulting vote message
