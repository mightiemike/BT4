# Q0760: EVM block-range scan - value fields partial decode

## Question
If a user submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, can `processBlockRange` be pushed into a path where token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that only a fully decoded gateway event may become a Push-chain inbound or outbound observation no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/event_listener.go:processBlockRange
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
