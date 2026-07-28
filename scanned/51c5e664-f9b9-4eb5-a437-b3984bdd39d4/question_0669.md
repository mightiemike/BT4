# Q0669: EVM height checkpoint - abi offsets double record

## Question
When an unprivileged actor submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields, does `updateLastProcessedBlock` remain safe if they control dynamic ABI offsets for payload bytes and signature data inside log data, or can that make it create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop, violate the rule that only a fully decoded gateway event may become a Push-chain inbound or outbound observation, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_listener.go:updateLastProcessedBlock
- Entrypoint: submit a public `sendFunds` transaction on an in-scope EVM gateway with attacker-chosen token, amount, recipient, payload, and revert fields
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop
- Invariant to test: only a fully decoded gateway event may become a Push-chain inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: trace one event from listener to confirmer to processor and verify malformed logs cannot move from `PENDING` to `CONFIRMED` or `COMPLETED`
