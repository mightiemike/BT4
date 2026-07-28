# Q1326: EVM resume height - ordering/finality early confirm

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `getStartBlock` remain safe if they control log ordering across adjacent blocks plus the exact reorg and confirmation timing, or can that make it misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early, violate the rule that reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:getStartBlock
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: log ordering across adjacent blocks plus the exact reorg and confirmation timing
- Exploit idea: misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early
- Invariant to test: reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a fork or local EVM devnet and compare raw log bytes against the persisted `store.Event` row and the resulting vote message
