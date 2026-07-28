# Q0763: EVM height checkpoint - value fields partial decode

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `updateLastProcessedBlock` remain safe if they control token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload, or can that make it accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, violate the rule that only a fully decoded gateway event may become a Push-chain inbound or outbound observation, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/event_listener.go:updateLastProcessedBlock
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
