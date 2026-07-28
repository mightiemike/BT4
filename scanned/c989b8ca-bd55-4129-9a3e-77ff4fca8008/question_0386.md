# Q0386: EVM resume height - abi offsets partial decode

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `getStartBlock` remain safe if they control dynamic ABI offsets for payload bytes and signature data inside log data, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/event_listener.go:getStartBlock
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: emit crafted gateway logs on a fork or local EVM devnet and compare raw log bytes against the persisted `store.Event` row and the resulting vote message
